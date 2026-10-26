/*
 * Tensor G5 (blazer) UFS root storage, read only.
 *
 * ABL shuts this controller down before starting Linux. Consequently this is
 * not a cold UFS/PHY driver: the Linux first stage must run Google's UFS,
 * UniPro, M-PHY, CPM and pin/power setup and then attest a live handoff with
 * blazer_ufs_live=1. This driver only takes over the legacy request list of
 * that live DWC host and issues single-slot, polled READ(10) and TEST UNIT
 * READY commands. No WRITE, UNMAP, query, task-management or NOP command is
 * ever constructed.
 *
 * Boot args:
 *   blazer_ufs=1       read the small, safe state-register whitelist only
 *   blazer_ufs=2       take over the list and publish /dev/ufsroot0
 *   blazer_ufs_live=1  mandatory first-stage attestation for either mode
 *   blazer_ufs_base=   host PA (default 0x3c400000, from the live FDT)
 *   blazer_ufs_root=   GPT partition name, REQUIRED for mode 2
 *   blazer_ufs_lun=    logical unit (default 0)
 *
 * Mode 1 has no side effects beyond MMIO reads. UIC error/status registers
 * 0x38..0x48 are clear-on-read and are never touched; nor is top register
 * 0x510. Mode 2 additionally refuses MCQ mode, an outstanding doorbell, a
 * not-ready host and any DMA address outside the G5's 36-bit reach. The root
 * partition has no default: guessing "userdata" and exposing the wrong bytes
 * is not a safe bring-up default.
 */
#include <kern/clock.h>
#include <kern/locks.h>
#include <libkern/libkern.h>
#include <mach/mach_types.h>
#include <mach/vm_param.h>
#include <miscfs/devfs/devfs.h>
#include <pexpert/pexpert.h>
#include <sys/buf.h>
#include <sys/conf.h>
#include <sys/disk.h>
#include <sys/errno.h>
#include <sys/hakios.blazer_ufs.h>
#include <sys/proc.h>
#include <sys/systm.h>
#include <sys/uio.h>
#include <vm/vm_kern.h>

extern vm_offset_t ml_io_map(vm_offset_t phys_addr, vm_size_t size);
extern uint64_t kvtophys(vm_offset_t va);
extern void FlushPoC_DcacheRegion(vm_offset_t va, size_t length);

#define UFS_CAP             0x00
#define UFS_VER             0x08
#define UFS_IS              0x20
#define UFS_IE              0x24
#define UFS_HCS             0x30
#define UFS_HCE             0x34
/* 0x38..0x48 are clear-on-read UIC error registers: deliberately absent. */
#define UFS_UTRLBA          0x50
#define UFS_UTRLBAU         0x54
#define UFS_UTRLDBR         0x58
#define UFS_UTRLCLR         0x5c
#define UFS_UTRLRSR         0x60
#define UFS_MEM_CFG         0x300

#define HCS_DP              (1u << 0)
#define HCS_UTRLRDY         (1u << 1)
#define CAP_64AS            (1u << 24)
#define MCQ_MODE_SELECT     (1u << 0)
#define UTRD_CT_UFS         (1u << 28)
#define UTRD_DD_IN          (2u << 25)
#define UTRD_DD_NONE        (0u << 25)
#define OCS_INVALID         0x0f
#define UPIU_COMMAND        0x01
#define UPIU_RESPONSE       0x21
#define UPIU_FLAG_READ      0x40
#define UPIU_IID_ONE        0x10       /* Google Laguna sets the header IID to one. */

#define UFS_BLOCK           4096u
#define BOUNCE_SIZE         (1u << 20)
#define PRDT_CHUNK          (256u << 10)
#define PRDT_MAX            (BOUNCE_SIZE / PRDT_CHUNK)
#define DMA_UTRL            0x0000
#define DMA_UCD             0x1000
#define DMA_BOUNCE          0x4000
#define DMA_SIZE            (DMA_BOUNCE + BOUNCE_SIZE)
#define G5_DMA_LIMIT        (1ULL << 36)
#define UFS_TIMEOUT_SECS    5

struct utrd {
	uint32_t dw0, dw1, dw2, dw3;
	uint32_t ucdba, ucdbau;
	uint32_t resp;
	uint32_t prdt;
};

struct ucd {
	uint8_t cmd[512];
	uint8_t resp[512];
	struct {
		uint32_t addr_lo, addr_hi, reserved, dbc;
	} prdt[PRDT_MAX];
};

struct blazer_ufs_state {
	vm_offset_t regs;
	vm_offset_t dma;
	uint64_t dma_pa;
	uint64_t hc_pa;
	uint32_t mode;
	uint8_t lun;
	uint8_t tag;
	uint64_t first_block, nblocks;
	uint32_t secsize;
	int bmajor, cmajor;
	dev_t bdev;
	bool ready;
	bool attached;
};

static struct blazer_ufs_state ufs;
static LCK_GRP_DECLARE(blazer_ufs_grp, "hakios-blazer-ufs");
static LCK_MTX_DECLARE(blazer_ufs_lock, &blazer_ufs_grp);

static inline uint32_t
ufs_read(uint32_t off)
{
	return *(volatile uint32_t *)(ufs.regs + off);
}

static inline void
ufs_write(uint32_t off, uint32_t value)
{
	__asm__ volatile ("dsb sy" ::: "memory");
	*(volatile uint32_t *)(ufs.regs + off) = value;
	__asm__ volatile ("dsb sy" ::: "memory");
}

static void
ufs_sync(vm_offset_t va, uint32_t len)
{
	FlushPoC_DcacheRegion(va, len);
}

static int
ufs_scsi(const uint8_t *cdb, unsigned cdb_len, uint32_t len)
{
	struct utrd *utrd = (struct utrd *)(ufs.dma + DMA_UTRL);
	struct ucd *ucd = (struct ucd *)(ufs.dma + DMA_UCD);
	uint64_t ucd_pa = ufs.dma_pa + DMA_UCD;
	uint64_t bounce_pa = ufs.dma_pa + DMA_BOUNCE;
	uint64_t list = ufs.dma_pa + DMA_UTRL;
	uint64_t deadline;
	uint32_t ocs;

	if (cdb_len != 6 && cdb_len != 10) {
		return EINVAL;
	}
	if (len > BOUNCE_SIZE || (cdb[0] != 0x00 && cdb[0] != 0x28)) {
		return EPERM;
	}
	if (cdb[0] == 0x00 && len != 0) {
		return EPERM;
	}
	bzero(utrd, sizeof(*utrd));
	bzero(ucd, sizeof(*ucd));
	ucd->cmd[0] = UPIU_COMMAND;
	ucd->cmd[1] = len != 0 ? UPIU_FLAG_READ : 0;
	ucd->cmd[2] = ufs.lun;
	ucd->cmd[3] = ++ufs.tag;
	ucd->cmd[4] = UPIU_IID_ONE;
	ucd->cmd[12] = (uint8_t)(len >> 24);
	ucd->cmd[13] = (uint8_t)(len >> 16);
	ucd->cmd[14] = (uint8_t)(len >> 8);
	ucd->cmd[15] = (uint8_t)len;
	memcpy(&ucd->cmd[16], cdb, cdb_len);

	utrd->dw0 = UTRD_CT_UFS | (len != 0 ? UTRD_DD_IN : UTRD_DD_NONE);
	utrd->dw2 = OCS_INVALID;
	utrd->ucdba = (uint32_t)ucd_pa;
	utrd->ucdbau = (uint32_t)(ucd_pa >> 32);
	utrd->resp = (512u / 4) << 16 | (512u / 4);
	if (len != 0) {
		uint32_t n = 0;

		for (uint32_t done = 0; done < len; done += PRDT_CHUNK, n++) {
			uint32_t chunk = len - done < PRDT_CHUNK ? len - done : PRDT_CHUNK;

			ucd->prdt[n].addr_lo = (uint32_t)(bounce_pa + done);
			ucd->prdt[n].addr_hi = (uint32_t)((bounce_pa + done) >> 32);
			ucd->prdt[n].dbc = chunk - 1;
		}
		utrd->prdt = (1024u / 4) << 16 | n;
	}
	/* Verify the exact command before it can reach a slot. There is no write
	 * command in this driver; this second check is intentionally redundant. */
	if ((ucd->cmd[0] & 0x3f) != UPIU_COMMAND || ucd->cmd[4] != UPIU_IID_ONE ||
	    (ucd->cmd[16] != 0x00 && ucd->cmd[16] != 0x28) ||
	    (len != 0 && (utrd->dw0 & UTRD_DD_IN) != UTRD_DD_IN)) {
		return EPERM;
	}
	ufs_sync((vm_offset_t)utrd, sizeof(*utrd));
	ufs_sync((vm_offset_t)ucd, sizeof(*ucd));
	ufs_sync(ufs.dma + DMA_BOUNCE, len);
	__asm__ volatile ("dsb sy" ::: "memory");

	/* Never ring unless our list is still the one in the host. */
	if (ufs_read(UFS_UTRLBA) != (uint32_t)list ||
	    ufs_read(UFS_UTRLBAU) != (uint32_t)(list >> 32) ||
	    (ufs_read(UFS_UTRLRSR) & 1) == 0 || ufs_read(UFS_UTRLDBR) != 0) {
		return EBUSY;
	}
	ufs_write(UFS_UTRLDBR, 1);
	clock_interval_to_deadline(UFS_TIMEOUT_SECS, NSEC_PER_SEC, &deadline);
	while ((ufs_read(UFS_UTRLDBR) & 1) != 0) {
		if (mach_absolute_time() > deadline) {
			ufs_write(UFS_UTRLCLR, 1);      /* only this driver's slot */
			printf("blazer-ufs: SCSI 0x%02x timed out\n", cdb[0]);
			return ETIMEDOUT;
		}
		delay(10);
	}
	__asm__ volatile ("dsb sy" ::: "memory");
	ufs_sync((vm_offset_t)utrd, sizeof(*utrd));
	ufs_sync((vm_offset_t)ucd, sizeof(*ucd));
	if (len != 0) {
		ufs_sync(ufs.dma + DMA_BOUNCE, len);
	}
	ocs = utrd->dw2 & 0xff;
	if (ocs != 0 || ucd->resp[0] != UPIU_RESPONSE || ucd->resp[3] != ufs.tag ||
	    ucd->resp[6] != 0 || (ucd->resp[7] != 0 && !(cdb[0] == 0x00 && ucd->resp[7] == 0x02))) {
		printf("blazer-ufs: SCSI 0x%02x failed: OCS %x UPIU %02x response %02x status %02x\n",
		    cdb[0], ocs, ucd->resp[0], ucd->resp[6], ucd->resp[7]);
		return EIO;
	}
	return 0;
}

static int
ufs_read_blocks(uint64_t lba, uint32_t count)
{
	uint8_t cdb[10] = {
		0x28, 0,
		(uint8_t)(lba >> 24), (uint8_t)(lba >> 16), (uint8_t)(lba >> 8), (uint8_t)lba,
		0, (uint8_t)(count >> 8), (uint8_t)count, 0
	};

	if (lba > UINT32_MAX || count == 0 || count > BOUNCE_SIZE / UFS_BLOCK) {
		return EINVAL;
	}
	return ufs_scsi(cdb, sizeof(cdb), count * UFS_BLOCK);
}

static int
ufs_find_partition(const char *want)
{
	const uint8_t *bytes = (const uint8_t *)(ufs.dma + DMA_BOUNCE);
	uint64_t entry_lba;
	uint32_t count, entry_size;
	int error = ufs_read_blocks(1, 1);

	if (error != 0) {
		return error;
	}
	if (memcmp(bytes, "EFI PART", 8) != 0) {
		printf("blazer-ufs: LUN %u has no GPT header\n", ufs.lun);
		return ENXIO;
	}
	memcpy(&entry_lba, bytes + 72, sizeof(entry_lba));
	memcpy(&count, bytes + 80, sizeof(count));
	memcpy(&entry_size, bytes + 84, sizeof(entry_size));
	if (entry_lba == 0 || entry_size != 128 || count == 0 ||
	    count > BOUNCE_SIZE / entry_size || entry_lba > UINT32_MAX) {
		printf("blazer-ufs: unsupported GPT table at %llu, %u entries of %u\n",
		    entry_lba, count, entry_size);
		return ENXIO;
	}
	error = ufs_read_blocks(entry_lba, (count * entry_size + UFS_BLOCK - 1) / UFS_BLOCK);
	if (error != 0) {
		return error;
	}
	for (uint32_t i = 0; i < count; i++) {
		const uint8_t *entry = bytes + i * entry_size;
		char name[37];
		unsigned c;
		uint64_t first, last;

		for (c = 0; c < 36; c++) {
			uint16_t ch = (uint16_t)(entry[56 + 2 * c] | entry[57 + 2 * c] << 8);

			if (ch == 0 || ch > 0x7f) {
				break;
			}
			name[c] = (char)ch;
		}
		name[c] = 0;
		if (strcmp(name, want) != 0) {
			continue;
		}
		memcpy(&first, entry + 32, sizeof(first));
		memcpy(&last, entry + 40, sizeof(last));
		if (last < first || last - first + 1 > UINT64_MAX / UFS_BLOCK) {
			return ENXIO;
		}
		ufs.first_block = first;
		ufs.nblocks = last - first + 1;
		return 0;
	}
	printf("blazer-ufs: no GPT partition named \"%s\"\n", want);
	return ENOENT;
}

static int
ufs_take_over(void)
{
	uint64_t list = ufs.dma_pa + DMA_UTRL;
	uint32_t hce = ufs_read(UFS_HCE), hcs = ufs_read(UFS_HCS);

	if ((hce & 1) == 0 || (hcs & (HCS_DP | HCS_UTRLRDY)) != (HCS_DP | HCS_UTRLRDY)) {
		printf("blazer-ufs: live host not ready (HCE %08x HCS %08x)\n", hce, hcs);
		return ENXIO;
	}
	if ((ufs_read(UFS_MEM_CFG) & MCQ_MODE_SELECT) != 0) {
		printf("blazer-ufs: host is in MCQ mode; the legacy doorbell is not safe to use\n");
		return ENOTSUP;
	}
	if (ufs_read(UFS_UTRLDBR) != 0) {
		printf("blazer-ufs: request outstanding from the first stage\n");
		return EBUSY;
	}
	if (ufs.dma_pa + DMA_SIZE > G5_DMA_LIMIT ||
	    (ufs.dma_pa + DMA_SIZE > (1ULL << 32) && (ufs_read(UFS_CAP) & CAP_64AS) == 0)) {
		printf("blazer-ufs: DMA at %llx is not addressable by this host\n", ufs.dma_pa);
		return ENXIO;
	}
	ufs_write(UFS_IE, 0);                   /* polled completion only */
	ufs_write(UFS_UTRLRSR, 0);
	ufs_write(UFS_UTRLBA, (uint32_t)list);
	ufs_write(UFS_UTRLBAU, (uint32_t)(list >> 32));
	ufs_write(UFS_UTRLRSR, 1);
	if (ufs_read(UFS_UTRLBA) != (uint32_t)list ||
	    ufs_read(UFS_UTRLBAU) != (uint32_t)(list >> 32) ||
	    (ufs_read(UFS_UTRLRSR) & 1) == 0) {
		printf("blazer-ufs: host did not take the legacy request list\n");
		return EIO;
	}
	return 0;
}

static int
ufs_open(__unused dev_t dev, int flags, __unused int devtype, __unused proc_t p)
{
	if (!ufs.ready) {
		return ENXIO;
	}
	return (flags & FWRITE) ? EROFS : 0;
}

static int
ufs_close(__unused dev_t dev, __unused int flags, __unused int devtype, __unused proc_t p)
{
	return 0;
}

static void
ufs_strategy(buf_t bp)
{
	uint64_t part_bytes = ufs.nblocks * UFS_BLOCK;
	uint64_t off = (uint64_t)buf_blkno(bp) * ufs.secsize;
	uint32_t count = buf_count(bp), done = 0;
	uint8_t *dst = (uint8_t *)buf_dataptr(bp);
	int error = 0;

	buf_setresid(bp, count);
	if (!ufs.ready) {
		error = ENXIO;
	} else if ((buf_flags(bp) & B_READ) == 0) {
		error = EROFS;
	} else if (off > part_bytes || count > part_bytes - off) {
		error = EINVAL;
	}
	if (error != 0 || count == 0) {
		if (error != 0) {
			buf_seterror(bp, error);
		}
		buf_biodone(bp);
		return;
	}

	lck_mtx_lock(&blazer_ufs_lock);
	while (done < count) {
		uint64_t pos = off + done;
		uint64_t lba = pos / UFS_BLOCK;
		uint32_t skip = (uint32_t)(pos % UFS_BLOCK);
		uint32_t want = count - done;
		uint32_t blocks;

		if (want > BOUNCE_SIZE - skip) {
			want = BOUNCE_SIZE - skip;
		}
		blocks = (skip + want + UFS_BLOCK - 1) / UFS_BLOCK;
		error = ufs_read_blocks(ufs.first_block + lba, blocks);
		if (error != 0) {
			break;
		}
		memcpy(dst + done, (const uint8_t *)(ufs.dma + DMA_BOUNCE) + skip, want);
		done += want;
	}
	lck_mtx_unlock(&blazer_ufs_lock);
	buf_setresid(bp, count - done);
	if (error != 0) {
		buf_seterror(bp, error);
	}
	buf_biodone(bp);
}

static int
ufs_ioctl_common(dev_t dev, u_long cmd, caddr_t data, __unused int flag,
    __unused proc_t p, int is_char)
{
	uint32_t *word = (uint32_t *)data;
	uint64_t *quad = (uint64_t *)data;

	(void)dev;
	if (!ufs.ready) {
		return ENXIO;
	}
	switch (cmd) {
	case DKIOCGETBLOCKSIZE:
		*word = ufs.secsize;
		return 0;
	case DKIOCSETBLOCKSIZE:
		if (is_char || *word < DEV_BSIZE || *word > UFS_BLOCK || (*word & (*word - 1)) != 0) {
			return EINVAL;
		}
		ufs.secsize = *word;
		return 0;
	case DKIOCGETPHYSICALBLOCKSIZE:
		*word = UFS_BLOCK;
		return 0;
	case DKIOCGETBLOCKCOUNT:
		*quad = ufs.nblocks * UFS_BLOCK / ufs.secsize;
		return 0;
	case DKIOCISWRITABLE:
		*word = 0;
		return 0;
	case DKIOCISSOLIDSTATE:
		*word = 1;
		return 0;
	case DKIOCGETMAXBLOCKCOUNTREAD:
		*quad = BOUNCE_SIZE / ufs.secsize;
		return 0;
	case DKIOCGETMAXBLOCKCOUNTWRITE:
		*quad = 0;
		return 0;
	default:
		return ENOTTY;
	}
}

static int
ufs_bioctl(dev_t dev, u_long cmd, caddr_t data, int flag, proc_t p)
{
	return ufs_ioctl_common(dev, cmd, data, flag, p, 0);
}

static int
ufs_cioctl(dev_t dev, u_long cmd, caddr_t data, int flag, proc_t p)
{
	return ufs_ioctl_common(dev, cmd, data, flag, p, 1);
}

static int
ufs_psize(__unused dev_t dev)
{
	return ufs.ready ? (int)MIN(ufs.nblocks * UFS_BLOCK / ufs.secsize, (uint64_t)INT_MAX) : -1;
}

static int
ufs_cread(dev_t dev, struct uio *uio, __unused int ioflag)
{
	return physio(ufs_strategy, NULL, dev, B_READ, minphys, uio, (int)ufs.secsize);
}

static const struct bdevsw ufs_bdevsw = {
	.d_open = ufs_open, .d_close = ufs_close, .d_strategy = ufs_strategy,
	.d_ioctl = ufs_bioctl, .d_dump = eno_dump, .d_psize = ufs_psize, .d_type = D_DISK,
};

static const struct cdevsw ufs_cdevsw = {
	.d_open = ufs_open, .d_close = ufs_close, .d_read = ufs_cread, .d_write = eno_rdwrt,
	.d_ioctl = ufs_cioctl, .d_stop = eno_stop, .d_reset = eno_reset, .d_ttys = NULL,
	.d_select = eno_select, .d_mmap = eno_mmap, .d_strategy = eno_strat,
	.d_reserved_1 = eno_getc, .d_reserved_2 = eno_putc, .d_type = D_DISK,
};

static int
ufs_attach(const char *want)
{
	int error;

	if (ufs.ready) {
		return 0;
	}
	if (ufs.attached) {
		return ENXIO;
	}
	ufs.attached = true;                    /* do not retry a half-taken host */
	if (kmem_alloc_contig(kernel_map, &ufs.dma, DMA_SIZE, PAGE_MASK, 0, 0,
	    KMA_KOBJECT | KMA_ZERO | KMA_DATA, VM_KERN_MEMORY_IOKIT) != KERN_SUCCESS) {
		printf("blazer-ufs: no contiguous DMA memory\n");
		return ENOMEM;
	}
	ufs.dma_pa = kvtophys(ufs.dma);
	if (ufs.dma_pa == 0) {
		return ENOMEM;
	}
	ufs.secsize = DEV_BSIZE;
	lck_mtx_lock(&blazer_ufs_lock);
	error = ufs_take_over();
	if (error == 0) {
		static const uint8_t tur[6] = { 0 };

		/* A first-stage handoff can leave unit attention pending. */
		for (unsigned i = 0; i < 3 && (error = ufs_scsi(tur, sizeof(tur), 0)) != 0; i++) {
		}
	}
	if (error == 0) {
		error = ufs_find_partition(want);
	}
	lck_mtx_unlock(&blazer_ufs_lock);
	if (error != 0) {
		return error;
	}

	ufs.bmajor = bdevsw_add(-1, &ufs_bdevsw);
	if (ufs.bmajor < 0) {
		return ENXIO;
	}
	ufs.cmajor = cdevsw_add_with_bdev(-1, &ufs_cdevsw, ufs.bmajor);
	if (ufs.cmajor < 0) {
		return ENXIO;
	}
	ufs.bdev = makedev(ufs.bmajor, 0);
	if (devfs_make_node(ufs.bdev, DEVFS_BLOCK, UID_ROOT, GID_OPERATOR, 0640, "ufsroot0") == NULL ||
	    devfs_make_node(makedev(ufs.cmajor, 0), DEVFS_CHAR, UID_ROOT, GID_OPERATOR,
	    0640, "rufsroot0") == NULL) {
		return ENXIO;
	}
	ufs.ready = true;
	printf("blazer-ufs: \"%s\" on LUN %u: first block %llx, %llu MiB, read only, DMA at %llx\n",
	    want, ufs.lun, ufs.first_block, ufs.nblocks * UFS_BLOCK >> 20, ufs.dma_pa);
	return 0;
}

void
hakios_blazer_ufs_init(void)
{
	uint32_t live = 0, lun = 0;
	char root[64];
	const char *reason = NULL;

	if (ufs.regs != 0 || !PE_parse_boot_argn("blazer_ufs", &ufs.mode, sizeof(ufs.mode)) || ufs.mode == 0) {
		return;
	}
	if (!PE_parse_boot_argn("blazer_ufs_live", &live, sizeof(live)) || live != 1) {
		printf("blazer-ufs: requested but no live first-stage handoff (blazer_ufs_live=1 absent)\n");
		return;
	}
	ufs.hc_pa = 0x3c400000ULL;
	PE_parse_boot_argn("blazer_ufs_base", &ufs.hc_pa, sizeof(ufs.hc_pa));
	if (ufs.hc_pa == 0 || (ufs.hc_pa & PAGE_MASK) != 0) {
		printf("blazer-ufs: invalid host address %llx\n", ufs.hc_pa);
		return;
	}
	PE_parse_boot_argn("blazer_ufs_lun", &lun, sizeof(lun));
	if (lun >= 8 || (ufs.mode != 1 && ufs.mode != 2)) {
		return;
	}
	ufs.lun = (uint8_t)lun;
	root[0] = 0;
	PE_parse_boot_arg_str("blazer_ufs_root", root, sizeof(root));
	if (ufs.mode == 2 && root[0] == 0) {
		printf("blazer-ufs: mode 2 requires blazer_ufs_root=NAME; no partition is exposed\n");
		return;
	}
	ufs.regs = ml_io_map((vm_offset_t)ufs.hc_pa, 0x1000);
	if (ufs.regs == 0) {
		printf("blazer-ufs: cannot map host at %llx\n", ufs.hc_pa);
		return;
	}
	printf("blazer-ufs: CAP %08x VER %08x HCE %08x HCS %08x AHIT %08x IE %08x\n",
	    ufs_read(UFS_CAP), ufs_read(UFS_VER), ufs_read(UFS_HCE), ufs_read(UFS_HCS),
	    ufs_read(0x18), ufs_read(UFS_IE));
	printf("blazer-ufs: list %08x:%08x run %08x doorbell %08x MEM_CFG %08x\n",
	    ufs_read(UFS_UTRLBAU), ufs_read(UFS_UTRLBA), ufs_read(UFS_UTRLRSR),
	    ufs_read(UFS_UTRLDBR), ufs_read(UFS_MEM_CFG));
	if (ufs.mode == 1) {
		printf("blazer-ufs: state probe only; no host register was written\n");
		return;
	}
	if ((ufs_read(UFS_CAP) & CAP_64AS) == 0) {
		reason = "host lacks 64-bit DMA capability";
	} else if (ufs_read(UFS_VER) < 0x200 || ufs_read(UFS_VER) >= 0x500) {
		reason = "unsupported UFSHCI version";
	}
	if (reason != NULL) {
		printf("blazer-ufs: takeover refused: %s\n", reason);
		return;
	}
	{
		int error = ufs_attach(root);

		if (error != 0) {
			printf("blazer-ufs: no root device (%d)\n", error);
		}
	}
}

int
hakios_blazer_ufs_rootdev(dev_t *dev)
{
	char root[64];

	if (dev == NULL) {
		return EINVAL;
	}
	if (!ufs.ready) {
		/* IOFindBSDRoot may reach us before bsd_init's optional driver init. */
		if (ufs.regs == 0) {
			hakios_blazer_ufs_init();
		}
		if (!ufs.ready) {
			if (ufs.mode != 2 || ufs.regs == 0) {
				return ENXIO;
			}
			root[0] = 0;
			PE_parse_boot_arg_str("blazer_ufs_root", root, sizeof(root));
			if (root[0] == 0) {
				return ENXIO;
			}
			{
				int error = ufs_attach(root);

				if (error != 0) {
					return error;
				}
			}
		}
	}
	*dev = ufs.bdev;
	return 0;
}

bool
hakios_blazer_ufs_dev(dev_t dev)
{
	return ufs.ready && ((ufs.bmajor >= 0 && major(dev) == ufs.bmajor) ||
	       (ufs.cmajor >= 0 && major(dev) == ufs.cmajor));
}
