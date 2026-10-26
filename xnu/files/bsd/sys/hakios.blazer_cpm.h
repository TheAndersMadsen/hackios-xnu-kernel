/*
 * Google Tensor G5 CPM mailbox transport (bsd/dev/hakios.blazer_cpm.c).
 * All entry points are optional and fail closed when the boot-arg gates are off.
 */
#ifndef _SYS_HAKIOS_BLAZER_CPM_H_
#define _SYS_HAKIOS_BLAZER_CPM_H_

#include <stdint.h>

#define HAKIOS_BLAZER_CPM_WORDS 4

void hakios_blazer_cpm_init(void);
int hakios_blazer_cpm_queue_init(const uint32_t words[HAKIOS_BLAZER_CPM_WORDS]);
int hakios_blazer_cpm_send(const uint32_t words[HAKIOS_BLAZER_CPM_WORDS]);
unsigned hakios_blazer_cpm_receive(uint32_t *out, unsigned max_msgs);

#endif /* _SYS_HAKIOS_BLAZER_CPM_H_ */
