/* ADT consumer for the display-owning first stage's validated framebuffer. */
#ifndef _ARM64_HAKIOS_BLAZER_DISPLAY_H_
#define _ARM64_HAKIOS_BLAZER_DISPLAY_H_

#include <stdbool.h>

void hakios_blazer_display_init(void);
bool hakios_blazer_display_clean_on_present(void);

#endif /* _ARM64_HAKIOS_BLAZER_DISPLAY_H_ */
