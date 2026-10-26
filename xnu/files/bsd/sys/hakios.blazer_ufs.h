/*
 * Read-only Tensor G5 UFS root storage (bsd/dev/hakios.blazer_ufs.c).
 */
#ifndef _SYS_HAKIOS_BLAZER_UFS_H_
#define _SYS_HAKIOS_BLAZER_UFS_H_

#include <stdbool.h>
#include <sys/types.h>

void hakios_blazer_ufs_init(void);
int hakios_blazer_ufs_rootdev(dev_t *dev);
bool hakios_blazer_ufs_dev(dev_t dev);

#endif /* _SYS_HAKIOS_BLAZER_UFS_H_ */
