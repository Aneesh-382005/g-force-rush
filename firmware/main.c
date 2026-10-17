/* tiltfire trigger: EK-TM4C123GXL (TM4C123GH6PM), bare metal, no TivaWare.
 *
 * SW1 (PF4) pressed -> sends "FIRE\n" on UART0 (the ICDI virtual COM port, 115200 8N1).
 * Host bytes drive the RGB LED: 'g' green flash (hit), 'r' red flash (hurt), 'o' off.
 * Clock stays on the 16 MHz PIOSC from reset, so no PLL setup is needed.
 */
#include <stdint.h>

#define REG(a) (*(volatile uint32_t *)(a))

#define SYSCTL_RCGCUART REG(0x400FE618)
#define SYSCTL_RCGCGPIO REG(0x400FE608)

#define GPIOA 0x40004000u
#define GPIOF 0x40025000u
#define GPIO_DATA(b, mask) REG((b) + ((mask) << 2))
#define GPIO_DIR(b) REG((b) + 0x400)
#define GPIO_AFSEL(b) REG((b) + 0x420)
#define GPIO_PUR(b) REG((b) + 0x510)
#define GPIO_DEN(b) REG((b) + 0x51C)
#define GPIO_PCTL(b) REG((b) + 0x52C)

#define UART0 0x4000C000u
#define UART_DR REG(UART0 + 0x000)
#define UART_FR REG(UART0 + 0x018)
#define UART_IBRD REG(UART0 + 0x024)
#define UART_FBRD REG(UART0 + 0x028)
#define UART_LCRH REG(UART0 + 0x02C)
#define UART_CTL REG(UART0 + 0x030)
#define UART_FR_TXFF 0x20u
#define UART_FR_RXFE 0x10u

#define SW1 (1u << 4)
#define LED_R (1u << 1)
#define LED_B (1u << 2)
#define LED_G (1u << 3)
#define LEDS (LED_R | LED_B | LED_G)

static void uart_puts(const char *s)
{
    while (*s) {
        while (UART_FR & UART_FR_TXFF) {}
        UART_DR = (uint8_t)*s++;
    }
}

static void delay_ms(uint32_t ms)
{
    /* about 4 cycles per loop pass at 16 MHz */
    for (volatile uint32_t n = ms * 4000u; n; n--) {}
}

int main(void)
{
    SYSCTL_RCGCUART |= 1u;            /* UART0 */
    SYSCTL_RCGCGPIO |= (1u << 0) | (1u << 5); /* ports A and F */
    delay_ms(1);                      /* clocks need a few cycles before register access */

    GPIO_AFSEL(GPIOA) |= 0x3u;        /* PA0 RX, PA1 TX */
    GPIO_PCTL(GPIOA) = (GPIO_PCTL(GPIOA) & ~0xFFu) | 0x11u;
    GPIO_DEN(GPIOA) |= 0x3u;

    UART_CTL = 0;
    UART_IBRD = 8;                    /* 16 MHz / (16 * 115200) = 8.6806 */
    UART_FBRD = 44;
    UART_LCRH = 0x70;                 /* 8 bits, FIFOs on */
    UART_CTL = 0x301;                 /* UART, TX, RX enabled */

    GPIO_DIR(GPIOF) |= LEDS;
    GPIO_DEN(GPIOF) |= LEDS | SW1;
    GPIO_PUR(GPIOF) |= SW1;           /* SW1 reads 0 when pressed */

    uint32_t prev = 0;
    uint32_t flash_left = 0;          /* ms the LED stays lit after a host command */
    GPIO_DATA(GPIOF, LEDS) = LED_B;   /* blue = ready */

    for (;;) {
        uint32_t down = (GPIO_DATA(GPIOF, SW1) & SW1) == 0;
        if (down && !prev) {
            uart_puts("FIRE\n");
            delay_ms(20);             /* debounce */
        }
        prev = down;

        while (!(UART_FR & UART_FR_RXFE)) {
            char c = (char)UART_DR;
            if (c == 'g') { GPIO_DATA(GPIOF, LEDS) = LED_G; flash_left = 120; }
            else if (c == 'r') { GPIO_DATA(GPIOF, LEDS) = LED_R; flash_left = 250; }
            else if (c == 'o') { GPIO_DATA(GPIOF, LEDS) = LED_B; flash_left = 0; }
        }

        delay_ms(1);
        if (flash_left && --flash_left == 0)
            GPIO_DATA(GPIOF, LEDS) = LED_B;
    }
}

/* ---- startup: vector table and reset handler ---- */
extern uint32_t _estack, _etext, _data, _edata, _bss, _ebss;

void Reset_Handler(void)
{
    uint32_t *src = &_etext, *dst = &_data;
    while (dst < &_edata) *dst++ = *src++;
    for (dst = &_bss; dst < &_ebss;) *dst++ = 0;
    main();
    for (;;) {}
}

static void Default_Handler(void) { for (;;) {} }

__attribute__((section(".isr_vector"), used))
void (*const vectors[])(void) = {
    (void (*)(void))&_estack,
    Reset_Handler,
    Default_Handler, /* NMI */
    Default_Handler, /* HardFault */
};
