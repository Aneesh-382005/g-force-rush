CC      = arm-none-eabi-gcc
OBJCOPY = arm-none-eabi-objcopy
CFLAGS  = -mcpu=cortex-m4 -mthumb -Os -Wall -Wextra -ffreestanding -fno-builtin
LDFLAGS = -T tm4c123.ld -nostdlib -Wl,--gc-sections

all: build/fire.bin

build/fire.elf: main.c tm4c123.ld
	@mkdir -p build
	$(CC) $(CFLAGS) main.c $(LDFLAGS) -o $@

build/fire.bin: build/fire.elf
	$(OBJCOPY) -O binary $< $@

# needs: sudo apt install openocd
flash: build/fire.elf
	openocd -f board/ti_ek-tm4c123gxl.cfg -c "program build/fire.elf verify reset exit"

clean:
	rm -rf build

.PHONY: all flash clean
