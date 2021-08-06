from utils import to_bin
from utils import to_hex
from utils import print_pins

from chips.pins import *

import numpy as np
_RD = lambda bits: np.random.randint(1<<bits)


class M65C02:
    def __init__(self):
        # all internal, and thus private, fields are marked with '_'.
        self._IR   = 0x00      # instruction register.
        self._PC   = 0x00      # program counter.
        self._AD   = 0x0000    # address register.
        self._A    = 0x00      # accumulator.
        self._X    = 0x00      # X register.
        self._Y    = 0x00      # Y register.
        self._S    = 0x00      # stack pointer.
        self._P    = M65C02_ZF # status register.
        self._PINS = 0         # last pins.

        self._irq_pip   = 0
        self._nmi_pip   = 0
        self._brk_flags = 0b000

        self._bcd_enabled = None

        self.circuit = None

    def attach_circuit(self, circuit):
        self.circuit = circuit

    def _SA(self, addr):
        """ set 16-bit address in 64-bit pin mask. """
        self.circuit.pins=(self.circuit.pins&(((1<<40)-1)&((((1<<16)-1)<<7)^((1<<40)-1))))|(addr<<7)

    def _GA(self):
        """ extract 16-bit addess from pin mask. """
        return (self.circuit.pins&(((1<<16)-1)<<7))>>7

    def _SAD(self, addr, data):
        """ set 16-bit address and 8-bit data in 64-bit pin mask. """
        self._SA(addr)
        self._SD(data)

    def _FETCH(self):
        """ fetch next opcode byte. """
        self._SA(self._PC)
        self._ON(M65C02_SYNC)

    def _SD(self, data):
        """ set 8-bit data in 64-bit pin mask. """
        self.circuit.pins=(self.circuit.pins&(((1<<40)-1)&((((1<<8)-1)<<23)^((1<<40)-1))))|(data<<23)

    def _GD(self):
        """ extract 8-bit data from 64-bit pin mask. """
        return (self.circuit.pins&(((1<<8)-1)<<23))>>23

    def _ON(self, m):
        """ enable control pins. """
        self.circuit.pins=self.circuit.pins|m

    def _OFF(self, m):
        """ disable control pins. """
        self.circuit.pins=self.circuit.pins&(m^((1<<40)-1))

    def _RD(self):
        """ a memory read tick. """
        self._ON(M65C02_RWB)

    def _WR(self):
        """ a memory write tick. """
        self._OFF(M65C02_RWB)

    def _NZ(self, v):
        """ set N and Z flags depending on value. """
        self._P=(self._P&((M65C02_NF|M65C02_ZF)^((1<<8)-1)))|((v&M65C02_NF) if (v&0xff) else (M65C02_ZF))

    def _INS(self):
        """ increment the stack pointer. """
        self._S=(self._S+1)%256

    def _DES(self):
        """ decrement the stack pointer. """
        self._S=(self._S-1)%256

    def _INCAD(self):
        """ increment the AD internal addr register. """
        self._AD=(self._AD+1)%65536

    def _DECAD(self):
        """ decrement the AD internal addr register. """
        self._AD=(self._AD-1)%65536

    def _INCPC(self):
        """ increment the Program Counter. """
        self._PC=(self._PC+1)%65536

    def _DECPC(self):
        """ decrement the Program Counter. """
        self._PC=(self._PC-1)%65536


    def tick(self, pins):
        if (not (self._PINS & M65C02_PHI2) and (pins & M65C02_PHI2)):  # ((pins & M65C02_PHI2) & ((M65C02_PHI2 & self._PINS) ^ ((1<< 40) - 1))):
            if ((pins & M65C02_SYNC) or not (pins & M65C02_IRQB) or not (pins & M65C02_NMIB) or (pins & M65C02_RDY) or not (pins & M65C02_RESB)):  # (pins & (M65C02_SYNC|M65C02_IRQB|M65C02_NMIB|M65C02_RDY|M65C02_RESB)):
                # NMIB: low-edge-transition triggered.
                if ((self._PINS & M65C02_NMIB) and not (pins & M65C02_NMIB)):  # (pins & ((pins ^ self._PINS) & M65C02_NMIB)):
                    print("NMIB")

                # IRQB: low-level triggered.
                if (not (pins & M65C02_IRQB) and not (self._P & M65C02_IF)):  # ((pins & M65C02_IRQB) and (not (self._P & M65C02_IF))):
                    print("IRQB")

                # check RDY during read cycles.
                if ((pins & (M65C02_RWB|M65C02_RDY)) == (M65C02_RWB|M65C02_RDY)):
                    pass
                    #print("RDY")

                if not (pins & M65C02_RESB):
                    self._brk_flags |= M65C02_BRK_RESET
                    self._IR  = 0
                    self._P  &= (M65C02_BF^((1<<8)-1))
                    self._PINS = pins
                    return pins

                if (pins & M65C02_SYNC):
                    self._IR = self._GD()<<3
                    self._OFF(M65C02_SYNC)

                    if (0 != (self._irq_pip & 4)):
                        print("IRQ")
                        self._brk_flags |= M65C02_BRK_IRQ
                        self._IR  = 0
                        self._P  &= (M65C02_BF^((1<<8)-1))

                    if (0 != (self._nmi_pip & 0xfffc)):
                        print("NMI")
                        self._brk_flags |= M65C02_BRK_NMI
                        self._IR  = 0
                        self._P  &= (M65C02_BF^((1<<8)-1))

                    self._irq_pip &= 3
                    self._nmi_pip &= 3

                    if not (self._brk_flags):
                        self._INCPC()

            self._RD()
            # BRK-s
            if   (self._IR == (0x00<<3|0)):self._SA(self._PC); # put PC on addr bus.
            elif (self._IR == (0x00<<3|1)):
                if (0==(self._brk_flags&(M65C02_BRK_IRQ|M65C02_BRK_NMI))): self._PC+=1;
                else:self._DES();self._SAD(0x0100|self._S,self._PC>>8);  # push MSB of PC onto stack.
                if(0==(self._brk_flags&M65C02_BRK_RESET)):self._WR(); # write to stack.
            elif (self._IR == (0x00<<3|2)):
                if (self._brk_flags&(M65C02_BRK_IRQ|M65C02_BRK_NMI)):self._DES();self._SAD(0x0100|self._S,self._PC);
                if(0==(self._brk_flags&M65C02_BRK_RESET)):self._WR();
            elif (self._IR == (0x00<<3|3)):
                if (self._brk_flags&(M65C02_BRK_IRQ|M65C02_BRK_NMI)):self._DES();self._SAD(0x0100|self._S,self._P|M65C02_XF);
                if(self._brk_flags&M65C02_BRK_RESET):self._AD=0xFFFC;
                else:
                    self._WR();
                    if(self._brk_flags&M65C02_BRK_NMI):self._AD=0xFFFA;
                    else:self._AD=0xFFFE;
            elif (self._IR == (0x00<<3|4)): self._SA(self._AD);self._INCAD();self._P|=(M65C02_IF|M65C02_BF);self._brk_flags=0; # RES/NMI hijacking.
            elif (self._IR == (0x00<<3|5)): self._SA(self._AD);self._AD=self._GD(); # NMI "half-hijacking" not possible.
            elif (self._IR == (0x00<<3|6)): self._PC=(self._GD()<<8)|self._AD;self._FETCH();
            elif (self._IR == (0x00<<3|7)): assert(False)

            # ORA-(zp,x)
            elif (self._IR == (0x01<<3|0)): assert(False);
            elif (self._IR == (0x01<<3|1)): assert(False);
            elif (self._IR == (0x01<<3|2)): assert(False);
            elif (self._IR == (0x01<<3|3)): assert(False);
            elif (self._IR == (0x01<<3|4)): assert(False);
            elif (self._IR == (0x01<<3|5)): assert(False);
            elif (self._IR == (0x01<<3|6)): assert(False);
            elif (self._IR == (0x01<<3|7)): assert(False);

            # None
            elif (self._IR == (0x02<<3|0)): assert(False);
            elif (self._IR == (0x02<<3|1)): assert(False);
            elif (self._IR == (0x02<<3|2)): assert(False);
            elif (self._IR == (0x02<<3|3)): assert(False);
            elif (self._IR == (0x02<<3|4)): assert(False);
            elif (self._IR == (0x02<<3|5)): assert(False);
            elif (self._IR == (0x02<<3|6)): assert(False);
            elif (self._IR == (0x02<<3|7)): assert(False);

            # None
            elif (self._IR == (0x03<<3|0)): assert(False);
            elif (self._IR == (0x03<<3|1)): assert(False);
            elif (self._IR == (0x03<<3|2)): assert(False);
            elif (self._IR == (0x03<<3|3)): assert(False);
            elif (self._IR == (0x03<<3|4)): assert(False);
            elif (self._IR == (0x03<<3|5)): assert(False);
            elif (self._IR == (0x03<<3|6)): assert(False);
            elif (self._IR == (0x03<<3|7)): assert(False);

            # TSB-zp
            elif (self._IR == (0x04<<3|0)): assert(False);
            elif (self._IR == (0x04<<3|1)): assert(False);
            elif (self._IR == (0x04<<3|2)): assert(False);
            elif (self._IR == (0x04<<3|3)): assert(False);
            elif (self._IR == (0x04<<3|4)): assert(False);
            elif (self._IR == (0x04<<3|5)): assert(False);
            elif (self._IR == (0x04<<3|6)): assert(False);
            elif (self._IR == (0x04<<3|7)): assert(False);

            # ORA-zp
            elif (self._IR == (0x05<<3|0)): assert(False);
            elif (self._IR == (0x05<<3|1)): assert(False);
            elif (self._IR == (0x05<<3|2)): assert(False);
            elif (self._IR == (0x05<<3|3)): assert(False);
            elif (self._IR == (0x05<<3|4)): assert(False);
            elif (self._IR == (0x05<<3|5)): assert(False);
            elif (self._IR == (0x05<<3|6)): assert(False);
            elif (self._IR == (0x05<<3|7)): assert(False);

            # ASL-zp
            elif (self._IR == (0x06<<3|0)): assert(False);
            elif (self._IR == (0x06<<3|1)): assert(False);
            elif (self._IR == (0x06<<3|2)): assert(False);
            elif (self._IR == (0x06<<3|3)): assert(False);
            elif (self._IR == (0x06<<3|4)): assert(False);
            elif (self._IR == (0x06<<3|5)): assert(False);
            elif (self._IR == (0x06<<3|6)): assert(False);
            elif (self._IR == (0x06<<3|7)): assert(False);

            # RMB0-zp
            elif (self._IR == (0x07<<3|0)): assert(False);
            elif (self._IR == (0x07<<3|1)): assert(False);
            elif (self._IR == (0x07<<3|2)): assert(False);
            elif (self._IR == (0x07<<3|3)): assert(False);
            elif (self._IR == (0x07<<3|4)): assert(False);
            elif (self._IR == (0x07<<3|5)): assert(False);
            elif (self._IR == (0x07<<3|6)): assert(False);
            elif (self._IR == (0x07<<3|7)): assert(False);

            # PHP-s
            elif (self._IR == (0x08<<3|0)): assert(False);
            elif (self._IR == (0x08<<3|1)): assert(False);
            elif (self._IR == (0x08<<3|2)): assert(False);
            elif (self._IR == (0x08<<3|3)): assert(False);
            elif (self._IR == (0x08<<3|4)): assert(False);
            elif (self._IR == (0x08<<3|5)): assert(False);
            elif (self._IR == (0x08<<3|6)): assert(False);
            elif (self._IR == (0x08<<3|7)): assert(False);

            # ORA-#
            elif (self._IR == (0x09<<3|0)): assert(False);
            elif (self._IR == (0x09<<3|1)): assert(False);
            elif (self._IR == (0x09<<3|2)): assert(False);
            elif (self._IR == (0x09<<3|3)): assert(False);
            elif (self._IR == (0x09<<3|4)): assert(False);
            elif (self._IR == (0x09<<3|5)): assert(False);
            elif (self._IR == (0x09<<3|6)): assert(False);
            elif (self._IR == (0x09<<3|7)): assert(False);

            # ASL-A
            elif (self._IR == (0x0a<<3|0)): assert(False);
            elif (self._IR == (0x0a<<3|1)): assert(False);
            elif (self._IR == (0x0a<<3|2)): assert(False);
            elif (self._IR == (0x0a<<3|3)): assert(False);
            elif (self._IR == (0x0a<<3|4)): assert(False);
            elif (self._IR == (0x0a<<3|5)): assert(False);
            elif (self._IR == (0x0a<<3|6)): assert(False);
            elif (self._IR == (0x0a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x0b<<3|0)): assert(False);
            elif (self._IR == (0x0b<<3|1)): assert(False);
            elif (self._IR == (0x0b<<3|2)): assert(False);
            elif (self._IR == (0x0b<<3|3)): assert(False);
            elif (self._IR == (0x0b<<3|4)): assert(False);
            elif (self._IR == (0x0b<<3|5)): assert(False);
            elif (self._IR == (0x0b<<3|6)): assert(False);
            elif (self._IR == (0x0b<<3|7)): assert(False);

            # TSB-a
            elif (self._IR == (0x0c<<3|0)): assert(False);
            elif (self._IR == (0x0c<<3|1)): assert(False);
            elif (self._IR == (0x0c<<3|2)): assert(False);
            elif (self._IR == (0x0c<<3|3)): assert(False);
            elif (self._IR == (0x0c<<3|4)): assert(False);
            elif (self._IR == (0x0c<<3|5)): assert(False);
            elif (self._IR == (0x0c<<3|6)): assert(False);
            elif (self._IR == (0x0c<<3|7)): assert(False);

            # ORA-a
            elif (self._IR == (0x0d<<3|0)): assert(False);
            elif (self._IR == (0x0d<<3|1)): assert(False);
            elif (self._IR == (0x0d<<3|2)): assert(False);
            elif (self._IR == (0x0d<<3|3)): assert(False);
            elif (self._IR == (0x0d<<3|4)): assert(False);
            elif (self._IR == (0x0d<<3|5)): assert(False);
            elif (self._IR == (0x0d<<3|6)): assert(False);
            elif (self._IR == (0x0d<<3|7)): assert(False);

            # ASL-a
            elif (self._IR == (0x0e<<3|0)): assert(False);
            elif (self._IR == (0x0e<<3|1)): assert(False);
            elif (self._IR == (0x0e<<3|2)): assert(False);
            elif (self._IR == (0x0e<<3|3)): assert(False);
            elif (self._IR == (0x0e<<3|4)): assert(False);
            elif (self._IR == (0x0e<<3|5)): assert(False);
            elif (self._IR == (0x0e<<3|6)): assert(False);
            elif (self._IR == (0x0e<<3|7)): assert(False);

            # BBR0-r
            elif (self._IR == (0x0f<<3|0)): assert(False);
            elif (self._IR == (0x0f<<3|1)): assert(False);
            elif (self._IR == (0x0f<<3|2)): assert(False);
            elif (self._IR == (0x0f<<3|3)): assert(False);
            elif (self._IR == (0x0f<<3|4)): assert(False);
            elif (self._IR == (0x0f<<3|5)): assert(False);
            elif (self._IR == (0x0f<<3|6)): assert(False);
            elif (self._IR == (0x0f<<3|7)): assert(False);



            # BPL-r
            elif (self._IR == (0x10<<3|0)): assert(False);
            elif (self._IR == (0x10<<3|1)): assert(False);
            elif (self._IR == (0x10<<3|2)): assert(False);
            elif (self._IR == (0x10<<3|3)): assert(False);
            elif (self._IR == (0x10<<3|4)): assert(False);
            elif (self._IR == (0x10<<3|5)): assert(False);
            elif (self._IR == (0x10<<3|6)): assert(False);
            elif (self._IR == (0x10<<3|7)): assert(False);

            # ORA-(zp),y
            elif (self._IR == (0x11<<3|0)): assert(False);
            elif (self._IR == (0x11<<3|1)): assert(False);
            elif (self._IR == (0x11<<3|2)): assert(False);
            elif (self._IR == (0x11<<3|3)): assert(False);
            elif (self._IR == (0x11<<3|4)): assert(False);
            elif (self._IR == (0x11<<3|5)): assert(False);
            elif (self._IR == (0x11<<3|6)): assert(False);
            elif (self._IR == (0x11<<3|7)): assert(False);

            # ORA-(zp)
            elif (self._IR == (0x12<<3|0)): assert(False);
            elif (self._IR == (0x12<<3|1)): assert(False);
            elif (self._IR == (0x12<<3|2)): assert(False);
            elif (self._IR == (0x12<<3|3)): assert(False);
            elif (self._IR == (0x12<<3|4)): assert(False);
            elif (self._IR == (0x12<<3|5)): assert(False);
            elif (self._IR == (0x12<<3|6)): assert(False);
            elif (self._IR == (0x12<<3|7)): assert(False);

            # None
            elif (self._IR == (0x13<<3|0)): assert(False);
            elif (self._IR == (0x13<<3|1)): assert(False);
            elif (self._IR == (0x13<<3|2)): assert(False);
            elif (self._IR == (0x13<<3|3)): assert(False);
            elif (self._IR == (0x13<<3|4)): assert(False);
            elif (self._IR == (0x13<<3|5)): assert(False);
            elif (self._IR == (0x13<<3|6)): assert(False);
            elif (self._IR == (0x13<<3|7)): assert(False);

            # TRB-zp
            elif (self._IR == (0x14<<3|0)): assert(False);
            elif (self._IR == (0x14<<3|1)): assert(False);
            elif (self._IR == (0x14<<3|2)): assert(False);
            elif (self._IR == (0x14<<3|3)): assert(False);
            elif (self._IR == (0x14<<3|4)): assert(False);
            elif (self._IR == (0x14<<3|5)): assert(False);
            elif (self._IR == (0x14<<3|6)): assert(False);
            elif (self._IR == (0x14<<3|7)): assert(False);

            # ORA-zp,x
            elif (self._IR == (0x15<<3|0)): assert(False);
            elif (self._IR == (0x15<<3|1)): assert(False);
            elif (self._IR == (0x15<<3|2)): assert(False);
            elif (self._IR == (0x15<<3|3)): assert(False);
            elif (self._IR == (0x15<<3|4)): assert(False);
            elif (self._IR == (0x15<<3|5)): assert(False);
            elif (self._IR == (0x15<<3|6)): assert(False);
            elif (self._IR == (0x15<<3|7)): assert(False);

            # ASL-zp,x
            elif (self._IR == (0x16<<3|0)): assert(False);
            elif (self._IR == (0x16<<3|1)): assert(False);
            elif (self._IR == (0x16<<3|2)): assert(False);
            elif (self._IR == (0x16<<3|3)): assert(False);
            elif (self._IR == (0x16<<3|4)): assert(False);
            elif (self._IR == (0x16<<3|5)): assert(False);
            elif (self._IR == (0x16<<3|6)): assert(False);
            elif (self._IR == (0x16<<3|7)): assert(False);

            # RMB1-zp
            elif (self._IR == (0x17<<3|0)): assert(False);
            elif (self._IR == (0x17<<3|1)): assert(False);
            elif (self._IR == (0x17<<3|2)): assert(False);
            elif (self._IR == (0x17<<3|3)): assert(False);
            elif (self._IR == (0x17<<3|4)): assert(False);
            elif (self._IR == (0x17<<3|5)): assert(False);
            elif (self._IR == (0x17<<3|6)): assert(False);
            elif (self._IR == (0x17<<3|7)): assert(False);

            # CLC-i
            elif (self._IR == (0x18<<3|0)): assert(False);
            elif (self._IR == (0x18<<3|1)): assert(False);
            elif (self._IR == (0x18<<3|2)): assert(False);
            elif (self._IR == (0x18<<3|3)): assert(False);
            elif (self._IR == (0x18<<3|4)): assert(False);
            elif (self._IR == (0x18<<3|5)): assert(False);
            elif (self._IR == (0x18<<3|6)): assert(False);
            elif (self._IR == (0x18<<3|7)): assert(False);

            # ORA-a,y
            elif (self._IR == (0x19<<3|0)): assert(False);
            elif (self._IR == (0x19<<3|1)): assert(False);
            elif (self._IR == (0x19<<3|2)): assert(False);
            elif (self._IR == (0x19<<3|3)): assert(False);
            elif (self._IR == (0x19<<3|4)): assert(False);
            elif (self._IR == (0x19<<3|5)): assert(False);
            elif (self._IR == (0x19<<3|6)): assert(False);
            elif (self._IR == (0x19<<3|7)): assert(False);

            # INC-A
            elif (self._IR == (0x1a<<3|0)): assert(False);
            elif (self._IR == (0x1a<<3|1)): assert(False);
            elif (self._IR == (0x1a<<3|2)): assert(False);
            elif (self._IR == (0x1a<<3|3)): assert(False);
            elif (self._IR == (0x1a<<3|4)): assert(False);
            elif (self._IR == (0x1a<<3|5)): assert(False);
            elif (self._IR == (0x1a<<3|6)): assert(False);
            elif (self._IR == (0x1a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x1b<<3|0)): assert(False);
            elif (self._IR == (0x1b<<3|1)): assert(False);
            elif (self._IR == (0x1b<<3|2)): assert(False);
            elif (self._IR == (0x1b<<3|3)): assert(False);
            elif (self._IR == (0x1b<<3|4)): assert(False);
            elif (self._IR == (0x1b<<3|5)): assert(False);
            elif (self._IR == (0x1b<<3|6)): assert(False);
            elif (self._IR == (0x1b<<3|7)): assert(False);

            # TRB-a
            elif (self._IR == (0x1c<<3|0)): assert(False);
            elif (self._IR == (0x1c<<3|1)): assert(False);
            elif (self._IR == (0x1c<<3|2)): assert(False);
            elif (self._IR == (0x1c<<3|3)): assert(False);
            elif (self._IR == (0x1c<<3|4)): assert(False);
            elif (self._IR == (0x1c<<3|5)): assert(False);
            elif (self._IR == (0x1c<<3|6)): assert(False);
            elif (self._IR == (0x1c<<3|7)): assert(False);

            # ORA-a,x
            elif (self._IR == (0x1d<<3|0)): assert(False);
            elif (self._IR == (0x1d<<3|1)): assert(False);
            elif (self._IR == (0x1d<<3|2)): assert(False);
            elif (self._IR == (0x1d<<3|3)): assert(False);
            elif (self._IR == (0x1d<<3|4)): assert(False);
            elif (self._IR == (0x1d<<3|5)): assert(False);
            elif (self._IR == (0x1d<<3|6)): assert(False);
            elif (self._IR == (0x1d<<3|7)): assert(False);

            # ASL-a,x
            elif (self._IR == (0x1e<<3|0)): assert(False);
            elif (self._IR == (0x1e<<3|1)): assert(False);
            elif (self._IR == (0x1e<<3|2)): assert(False);
            elif (self._IR == (0x1e<<3|3)): assert(False);
            elif (self._IR == (0x1e<<3|4)): assert(False);
            elif (self._IR == (0x1e<<3|5)): assert(False);
            elif (self._IR == (0x1e<<3|6)): assert(False);
            elif (self._IR == (0x1e<<3|7)): assert(False);

            # BBR1-r
            elif (self._IR == (0x1f<<3|0)): assert(False);
            elif (self._IR == (0x1f<<3|1)): assert(False);
            elif (self._IR == (0x1f<<3|2)): assert(False);
            elif (self._IR == (0x1f<<3|3)): assert(False);
            elif (self._IR == (0x1f<<3|4)): assert(False);
            elif (self._IR == (0x1f<<3|5)): assert(False);
            elif (self._IR == (0x1f<<3|6)): assert(False);
            elif (self._IR == (0x1f<<3|7)): assert(False);



            # JSR-a
            elif (self._IR == (0x20<<3|0)): assert(False);
            elif (self._IR == (0x20<<3|1)): assert(False);
            elif (self._IR == (0x20<<3|2)): assert(False);
            elif (self._IR == (0x20<<3|3)): assert(False);
            elif (self._IR == (0x20<<3|4)): assert(False);
            elif (self._IR == (0x20<<3|5)): assert(False);
            elif (self._IR == (0x20<<3|6)): assert(False);
            elif (self._IR == (0x20<<3|7)): assert(False);

            # AND-(zp,x)
            elif (self._IR == (0x21<<3|0)): assert(False);
            elif (self._IR == (0x21<<3|1)): assert(False);
            elif (self._IR == (0x21<<3|2)): assert(False);
            elif (self._IR == (0x21<<3|3)): assert(False);
            elif (self._IR == (0x21<<3|4)): assert(False);
            elif (self._IR == (0x21<<3|5)): assert(False);
            elif (self._IR == (0x21<<3|6)): assert(False);
            elif (self._IR == (0x21<<3|7)): assert(False);

            # None
            elif (self._IR == (0x22<<3|0)): assert(False);
            elif (self._IR == (0x22<<3|1)): assert(False);
            elif (self._IR == (0x22<<3|2)): assert(False);
            elif (self._IR == (0x22<<3|3)): assert(False);
            elif (self._IR == (0x22<<3|4)): assert(False);
            elif (self._IR == (0x22<<3|5)): assert(False);
            elif (self._IR == (0x22<<3|6)): assert(False);
            elif (self._IR == (0x22<<3|7)): assert(False);

            # None
            elif (self._IR == (0x23<<3|0)): assert(False);
            elif (self._IR == (0x23<<3|1)): assert(False);
            elif (self._IR == (0x23<<3|2)): assert(False);
            elif (self._IR == (0x23<<3|3)): assert(False);
            elif (self._IR == (0x23<<3|4)): assert(False);
            elif (self._IR == (0x23<<3|5)): assert(False);
            elif (self._IR == (0x23<<3|6)): assert(False);
            elif (self._IR == (0x23<<3|7)): assert(False);

            # BIT-zp
            elif (self._IR == (0x24<<3|0)): assert(False);
            elif (self._IR == (0x24<<3|1)): assert(False);
            elif (self._IR == (0x24<<3|2)): assert(False);
            elif (self._IR == (0x24<<3|3)): assert(False);
            elif (self._IR == (0x24<<3|4)): assert(False);
            elif (self._IR == (0x24<<3|5)): assert(False);
            elif (self._IR == (0x24<<3|6)): assert(False);
            elif (self._IR == (0x24<<3|7)): assert(False);

            # AND-zp
            elif (self._IR == (0x25<<3|0)): assert(False);
            elif (self._IR == (0x25<<3|1)): assert(False);
            elif (self._IR == (0x25<<3|2)): assert(False);
            elif (self._IR == (0x25<<3|3)): assert(False);
            elif (self._IR == (0x25<<3|4)): assert(False);
            elif (self._IR == (0x25<<3|5)): assert(False);
            elif (self._IR == (0x25<<3|6)): assert(False);
            elif (self._IR == (0x25<<3|7)): assert(False);

            # ROL-zp
            elif (self._IR == (0x26<<3|0)): assert(False);
            elif (self._IR == (0x26<<3|1)): assert(False);
            elif (self._IR == (0x26<<3|2)): assert(False);
            elif (self._IR == (0x26<<3|3)): assert(False);
            elif (self._IR == (0x26<<3|4)): assert(False);
            elif (self._IR == (0x26<<3|5)): assert(False);
            elif (self._IR == (0x26<<3|6)): assert(False);
            elif (self._IR == (0x26<<3|7)): assert(False);

            # RMB2-zp
            elif (self._IR == (0x27<<3|0)): assert(False);
            elif (self._IR == (0x27<<3|1)): assert(False);
            elif (self._IR == (0x27<<3|2)): assert(False);
            elif (self._IR == (0x27<<3|3)): assert(False);
            elif (self._IR == (0x27<<3|4)): assert(False);
            elif (self._IR == (0x27<<3|5)): assert(False);
            elif (self._IR == (0x27<<3|6)): assert(False);
            elif (self._IR == (0x27<<3|7)): assert(False);

            # PLP-s
            elif (self._IR == (0x28<<3|0)): assert(False);
            elif (self._IR == (0x28<<3|1)): assert(False);
            elif (self._IR == (0x28<<3|2)): assert(False);
            elif (self._IR == (0x28<<3|3)): assert(False);
            elif (self._IR == (0x28<<3|4)): assert(False);
            elif (self._IR == (0x28<<3|5)): assert(False);
            elif (self._IR == (0x28<<3|6)): assert(False);
            elif (self._IR == (0x28<<3|7)): assert(False);

            # AND-#
            elif (self._IR == (0x29<<3|0)): assert(False);
            elif (self._IR == (0x29<<3|1)): assert(False);
            elif (self._IR == (0x29<<3|2)): assert(False);
            elif (self._IR == (0x29<<3|3)): assert(False);
            elif (self._IR == (0x29<<3|4)): assert(False);
            elif (self._IR == (0x29<<3|5)): assert(False);
            elif (self._IR == (0x29<<3|6)): assert(False);
            elif (self._IR == (0x29<<3|7)): assert(False);

            # ROL-A
            elif (self._IR == (0x2a<<3|0)): assert(False);
            elif (self._IR == (0x2a<<3|1)): assert(False);
            elif (self._IR == (0x2a<<3|2)): assert(False);
            elif (self._IR == (0x2a<<3|3)): assert(False);
            elif (self._IR == (0x2a<<3|4)): assert(False);
            elif (self._IR == (0x2a<<3|5)): assert(False);
            elif (self._IR == (0x2a<<3|6)): assert(False);
            elif (self._IR == (0x2a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x2b<<3|0)): assert(False);
            elif (self._IR == (0x2b<<3|1)): assert(False);
            elif (self._IR == (0x2b<<3|2)): assert(False);
            elif (self._IR == (0x2b<<3|3)): assert(False);
            elif (self._IR == (0x2b<<3|4)): assert(False);
            elif (self._IR == (0x2b<<3|5)): assert(False);
            elif (self._IR == (0x2b<<3|6)): assert(False);
            elif (self._IR == (0x2b<<3|7)): assert(False);

            # BIT-a
            elif (self._IR == (0x2c<<3|0)): assert(False);
            elif (self._IR == (0x2c<<3|1)): assert(False);
            elif (self._IR == (0x2c<<3|2)): assert(False);
            elif (self._IR == (0x2c<<3|3)): assert(False);
            elif (self._IR == (0x2c<<3|4)): assert(False);
            elif (self._IR == (0x2c<<3|5)): assert(False);
            elif (self._IR == (0x2c<<3|6)): assert(False);
            elif (self._IR == (0x2c<<3|7)): assert(False);

            # AND-a
            elif (self._IR == (0x2d<<3|0)): assert(False);
            elif (self._IR == (0x2d<<3|1)): assert(False);
            elif (self._IR == (0x2d<<3|2)): assert(False);
            elif (self._IR == (0x2d<<3|3)): assert(False);
            elif (self._IR == (0x2d<<3|4)): assert(False);
            elif (self._IR == (0x2d<<3|5)): assert(False);
            elif (self._IR == (0x2d<<3|6)): assert(False);
            elif (self._IR == (0x2d<<3|7)): assert(False);

            # ROL-a
            elif (self._IR == (0x2e<<3|0)): assert(False);
            elif (self._IR == (0x2e<<3|1)): assert(False);
            elif (self._IR == (0x2e<<3|2)): assert(False);
            elif (self._IR == (0x2e<<3|3)): assert(False);
            elif (self._IR == (0x2e<<3|4)): assert(False);
            elif (self._IR == (0x2e<<3|5)): assert(False);
            elif (self._IR == (0x2e<<3|6)): assert(False);
            elif (self._IR == (0x2e<<3|7)): assert(False);

            # BBR2-r
            elif (self._IR == (0x2f<<3|0)): assert(False);
            elif (self._IR == (0x2f<<3|1)): assert(False);
            elif (self._IR == (0x2f<<3|2)): assert(False);
            elif (self._IR == (0x2f<<3|3)): assert(False);
            elif (self._IR == (0x2f<<3|4)): assert(False);
            elif (self._IR == (0x2f<<3|5)): assert(False);
            elif (self._IR == (0x2f<<3|6)): assert(False);
            elif (self._IR == (0x2f<<3|7)): assert(False);



            # BMI-r
            elif (self._IR == (0x30<<3|0)): assert(False);
            elif (self._IR == (0x30<<3|1)): assert(False);
            elif (self._IR == (0x30<<3|2)): assert(False);
            elif (self._IR == (0x30<<3|3)): assert(False);
            elif (self._IR == (0x30<<3|4)): assert(False);
            elif (self._IR == (0x30<<3|5)): assert(False);
            elif (self._IR == (0x30<<3|6)): assert(False);
            elif (self._IR == (0x30<<3|7)): assert(False);

            # AND-(zp),y
            elif (self._IR == (0x31<<3|0)): assert(False);
            elif (self._IR == (0x31<<3|1)): assert(False);
            elif (self._IR == (0x31<<3|2)): assert(False);
            elif (self._IR == (0x31<<3|3)): assert(False);
            elif (self._IR == (0x31<<3|4)): assert(False);
            elif (self._IR == (0x31<<3|5)): assert(False);
            elif (self._IR == (0x31<<3|6)): assert(False);
            elif (self._IR == (0x31<<3|7)): assert(False);

            # AND-(zp)
            elif (self._IR == (0x32<<3|0)): assert(False);
            elif (self._IR == (0x32<<3|1)): assert(False);
            elif (self._IR == (0x32<<3|2)): assert(False);
            elif (self._IR == (0x32<<3|3)): assert(False);
            elif (self._IR == (0x32<<3|4)): assert(False);
            elif (self._IR == (0x32<<3|5)): assert(False);
            elif (self._IR == (0x32<<3|6)): assert(False);
            elif (self._IR == (0x32<<3|7)): assert(False);

            # None
            elif (self._IR == (0x33<<3|0)): assert(False);
            elif (self._IR == (0x33<<3|1)): assert(False);
            elif (self._IR == (0x33<<3|2)): assert(False);
            elif (self._IR == (0x33<<3|3)): assert(False);
            elif (self._IR == (0x33<<3|4)): assert(False);
            elif (self._IR == (0x33<<3|5)): assert(False);
            elif (self._IR == (0x33<<3|6)): assert(False);
            elif (self._IR == (0x33<<3|7)): assert(False);

            # BIT-zp,x
            elif (self._IR == (0x34<<3|0)): assert(False);
            elif (self._IR == (0x34<<3|1)): assert(False);
            elif (self._IR == (0x34<<3|2)): assert(False);
            elif (self._IR == (0x34<<3|3)): assert(False);
            elif (self._IR == (0x34<<3|4)): assert(False);
            elif (self._IR == (0x34<<3|5)): assert(False);
            elif (self._IR == (0x34<<3|6)): assert(False);
            elif (self._IR == (0x34<<3|7)): assert(False);

            # AND-zp,x
            elif (self._IR == (0x35<<3|0)): assert(False);
            elif (self._IR == (0x35<<3|1)): assert(False);
            elif (self._IR == (0x35<<3|2)): assert(False);
            elif (self._IR == (0x35<<3|3)): assert(False);
            elif (self._IR == (0x35<<3|4)): assert(False);
            elif (self._IR == (0x35<<3|5)): assert(False);
            elif (self._IR == (0x35<<3|6)): assert(False);
            elif (self._IR == (0x35<<3|7)): assert(False);

            # ROL-zp,x
            elif (self._IR == (0x36<<3|0)): assert(False);
            elif (self._IR == (0x36<<3|1)): assert(False);
            elif (self._IR == (0x36<<3|2)): assert(False);
            elif (self._IR == (0x36<<3|3)): assert(False);
            elif (self._IR == (0x36<<3|4)): assert(False);
            elif (self._IR == (0x36<<3|5)): assert(False);
            elif (self._IR == (0x36<<3|6)): assert(False);
            elif (self._IR == (0x36<<3|7)): assert(False);

            # RMB3-zp
            elif (self._IR == (0x37<<3|0)): assert(False);
            elif (self._IR == (0x37<<3|1)): assert(False);
            elif (self._IR == (0x37<<3|2)): assert(False);
            elif (self._IR == (0x37<<3|3)): assert(False);
            elif (self._IR == (0x37<<3|4)): assert(False);
            elif (self._IR == (0x37<<3|5)): assert(False);
            elif (self._IR == (0x37<<3|6)): assert(False);
            elif (self._IR == (0x37<<3|7)): assert(False);

            # SEC-I
            elif (self._IR == (0x38<<3|0)): assert(False);
            elif (self._IR == (0x38<<3|1)): assert(False);
            elif (self._IR == (0x38<<3|2)): assert(False);
            elif (self._IR == (0x38<<3|3)): assert(False);
            elif (self._IR == (0x38<<3|4)): assert(False);
            elif (self._IR == (0x38<<3|5)): assert(False);
            elif (self._IR == (0x38<<3|6)): assert(False);
            elif (self._IR == (0x38<<3|7)): assert(False);

            # AND-a,y
            elif (self._IR == (0x39<<3|0)): assert(False);
            elif (self._IR == (0x39<<3|1)): assert(False);
            elif (self._IR == (0x39<<3|2)): assert(False);
            elif (self._IR == (0x39<<3|3)): assert(False);
            elif (self._IR == (0x39<<3|4)): assert(False);
            elif (self._IR == (0x39<<3|5)): assert(False);
            elif (self._IR == (0x39<<3|6)): assert(False);
            elif (self._IR == (0x39<<3|7)): assert(False);

            # DEC-A
            elif (self._IR == (0x3a<<3|0)): assert(False);
            elif (self._IR == (0x3a<<3|1)): assert(False);
            elif (self._IR == (0x3a<<3|2)): assert(False);
            elif (self._IR == (0x3a<<3|3)): assert(False);
            elif (self._IR == (0x3a<<3|4)): assert(False);
            elif (self._IR == (0x3a<<3|5)): assert(False);
            elif (self._IR == (0x3a<<3|6)): assert(False);
            elif (self._IR == (0x3a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x3b<<3|0)): assert(False);
            elif (self._IR == (0x3b<<3|1)): assert(False);
            elif (self._IR == (0x3b<<3|2)): assert(False);
            elif (self._IR == (0x3b<<3|3)): assert(False);
            elif (self._IR == (0x3b<<3|4)): assert(False);
            elif (self._IR == (0x3b<<3|5)): assert(False);
            elif (self._IR == (0x3b<<3|6)): assert(False);
            elif (self._IR == (0x3b<<3|7)): assert(False);

            # BIT-a,x
            elif (self._IR == (0x3c<<3|0)): assert(False);
            elif (self._IR == (0x3c<<3|1)): assert(False);
            elif (self._IR == (0x3c<<3|2)): assert(False);
            elif (self._IR == (0x3c<<3|3)): assert(False);
            elif (self._IR == (0x3c<<3|4)): assert(False);
            elif (self._IR == (0x3c<<3|5)): assert(False);
            elif (self._IR == (0x3c<<3|6)): assert(False);
            elif (self._IR == (0x3c<<3|7)): assert(False);

            # AND-a,x
            elif (self._IR == (0x3d<<3|0)): assert(False);
            elif (self._IR == (0x3d<<3|1)): assert(False);
            elif (self._IR == (0x3d<<3|2)): assert(False);
            elif (self._IR == (0x3d<<3|3)): assert(False);
            elif (self._IR == (0x3d<<3|4)): assert(False);
            elif (self._IR == (0x3d<<3|5)): assert(False);
            elif (self._IR == (0x3d<<3|6)): assert(False);
            elif (self._IR == (0x3d<<3|7)): assert(False);

            # ROL-a,x
            elif (self._IR == (0x3e<<3|0)): assert(False);
            elif (self._IR == (0x3e<<3|1)): assert(False);
            elif (self._IR == (0x3e<<3|2)): assert(False);
            elif (self._IR == (0x3e<<3|3)): assert(False);
            elif (self._IR == (0x3e<<3|4)): assert(False);
            elif (self._IR == (0x3e<<3|5)): assert(False);
            elif (self._IR == (0x3e<<3|6)): assert(False);
            elif (self._IR == (0x3e<<3|7)): assert(False);

            # BBR3-r
            elif (self._IR == (0x3f<<3|0)): assert(False);
            elif (self._IR == (0x3f<<3|1)): assert(False);
            elif (self._IR == (0x3f<<3|2)): assert(False);
            elif (self._IR == (0x3f<<3|3)): assert(False);
            elif (self._IR == (0x3f<<3|4)): assert(False);
            elif (self._IR == (0x3f<<3|5)): assert(False);
            elif (self._IR == (0x3f<<3|6)): assert(False);
            elif (self._IR == (0x3f<<3|7)): assert(False);



            # RTI-s
            elif (self._IR == (0x40<<3|0)): assert(False);
            elif (self._IR == (0x40<<3|1)): assert(False);
            elif (self._IR == (0x40<<3|2)): assert(False);
            elif (self._IR == (0x40<<3|3)): assert(False);
            elif (self._IR == (0x40<<3|4)): assert(False);
            elif (self._IR == (0x40<<3|5)): assert(False);
            elif (self._IR == (0x40<<3|6)): assert(False);
            elif (self._IR == (0x40<<3|7)): assert(False);

            # EOR-(zp,x)
            elif (self._IR == (0x41<<3|0)): assert(False);
            elif (self._IR == (0x41<<3|1)): assert(False);
            elif (self._IR == (0x41<<3|2)): assert(False);
            elif (self._IR == (0x41<<3|3)): assert(False);
            elif (self._IR == (0x41<<3|4)): assert(False);
            elif (self._IR == (0x41<<3|5)): assert(False);
            elif (self._IR == (0x41<<3|6)): assert(False);
            elif (self._IR == (0x41<<3|7)): assert(False);

            # None
            elif (self._IR == (0x42<<3|0)): assert(False);
            elif (self._IR == (0x42<<3|1)): assert(False);
            elif (self._IR == (0x42<<3|2)): assert(False);
            elif (self._IR == (0x42<<3|3)): assert(False);
            elif (self._IR == (0x42<<3|4)): assert(False);
            elif (self._IR == (0x42<<3|5)): assert(False);
            elif (self._IR == (0x42<<3|6)): assert(False);
            elif (self._IR == (0x42<<3|7)): assert(False);

            # None
            elif (self._IR == (0x43<<3|0)): assert(False);
            elif (self._IR == (0x43<<3|1)): assert(False);
            elif (self._IR == (0x43<<3|2)): assert(False);
            elif (self._IR == (0x43<<3|3)): assert(False);
            elif (self._IR == (0x43<<3|4)): assert(False);
            elif (self._IR == (0x43<<3|5)): assert(False);
            elif (self._IR == (0x43<<3|6)): assert(False);
            elif (self._IR == (0x43<<3|7)): assert(False);

            # None
            elif (self._IR == (0x44<<3|0)): assert(False);
            elif (self._IR == (0x44<<3|1)): assert(False);
            elif (self._IR == (0x44<<3|2)): assert(False);
            elif (self._IR == (0x44<<3|3)): assert(False);
            elif (self._IR == (0x44<<3|4)): assert(False);
            elif (self._IR == (0x44<<3|5)): assert(False);
            elif (self._IR == (0x44<<3|6)): assert(False);
            elif (self._IR == (0x44<<3|7)): assert(False);

            # EOR-zp
            elif (self._IR == (0x45<<3|0)): assert(False);
            elif (self._IR == (0x45<<3|1)): assert(False);
            elif (self._IR == (0x45<<3|2)): assert(False);
            elif (self._IR == (0x45<<3|3)): assert(False);
            elif (self._IR == (0x45<<3|4)): assert(False);
            elif (self._IR == (0x45<<3|5)): assert(False);
            elif (self._IR == (0x45<<3|6)): assert(False);
            elif (self._IR == (0x45<<3|7)): assert(False);

            # LSR-zp
            elif (self._IR == (0x46<<3|0)): assert(False);
            elif (self._IR == (0x46<<3|1)): assert(False);
            elif (self._IR == (0x46<<3|2)): assert(False);
            elif (self._IR == (0x46<<3|3)): assert(False);
            elif (self._IR == (0x46<<3|4)): assert(False);
            elif (self._IR == (0x46<<3|5)): assert(False);
            elif (self._IR == (0x46<<3|6)): assert(False);
            elif (self._IR == (0x46<<3|7)): assert(False);

            # RMB4-zp
            elif (self._IR == (0x47<<3|0)): assert(False);
            elif (self._IR == (0x47<<3|1)): assert(False);
            elif (self._IR == (0x47<<3|2)): assert(False);
            elif (self._IR == (0x47<<3|3)): assert(False);
            elif (self._IR == (0x47<<3|4)): assert(False);
            elif (self._IR == (0x47<<3|5)): assert(False);
            elif (self._IR == (0x47<<3|6)): assert(False);
            elif (self._IR == (0x47<<3|7)): assert(False);

            # PHA-s
            elif (self._IR == (0x48<<3|0)): assert(False);
            elif (self._IR == (0x48<<3|1)): assert(False);
            elif (self._IR == (0x48<<3|2)): assert(False);
            elif (self._IR == (0x48<<3|3)): assert(False);
            elif (self._IR == (0x48<<3|4)): assert(False);
            elif (self._IR == (0x48<<3|5)): assert(False);
            elif (self._IR == (0x48<<3|6)): assert(False);
            elif (self._IR == (0x48<<3|7)): assert(False);

            # EOR-#
            elif (self._IR == (0x49<<3|0)): assert(False);
            elif (self._IR == (0x49<<3|1)): assert(False);
            elif (self._IR == (0x49<<3|2)): assert(False);
            elif (self._IR == (0x49<<3|3)): assert(False);
            elif (self._IR == (0x49<<3|4)): assert(False);
            elif (self._IR == (0x49<<3|5)): assert(False);
            elif (self._IR == (0x49<<3|6)): assert(False);
            elif (self._IR == (0x49<<3|7)): assert(False);

            # LSR-A
            elif (self._IR == (0x4a<<3|0)): assert(False);
            elif (self._IR == (0x4a<<3|1)): assert(False);
            elif (self._IR == (0x4a<<3|2)): assert(False);
            elif (self._IR == (0x4a<<3|3)): assert(False);
            elif (self._IR == (0x4a<<3|4)): assert(False);
            elif (self._IR == (0x4a<<3|5)): assert(False);
            elif (self._IR == (0x4a<<3|6)): assert(False);
            elif (self._IR == (0x4a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x4b<<3|0)): assert(False);
            elif (self._IR == (0x4b<<3|1)): assert(False);
            elif (self._IR == (0x4b<<3|2)): assert(False);
            elif (self._IR == (0x4b<<3|3)): assert(False);
            elif (self._IR == (0x4b<<3|4)): assert(False);
            elif (self._IR == (0x4b<<3|5)): assert(False);
            elif (self._IR == (0x4b<<3|6)): assert(False);
            elif (self._IR == (0x4b<<3|7)): assert(False);

            # JMP-a
            elif (self._IR == (0x4c<<3|0)): self._SA(self._PC); self._INCPC();
            elif (self._IR == (0x4c<<3|1)): self._SA(self._PC); self._INCPC(); self._AD=self._GD();
            elif (self._IR == (0x4c<<3|2)): self._PC=(self._GD()<<8)|self._AD;self._FETCH();
            elif (self._IR == (0x4c<<3|3)): assert(False);
            elif (self._IR == (0x4c<<3|4)): assert(False);
            elif (self._IR == (0x4c<<3|5)): assert(False);
            elif (self._IR == (0x4c<<3|6)): assert(False);
            elif (self._IR == (0x4c<<3|7)): assert(False);

            # EOR-a
            elif (self._IR == (0x4d<<3|0)): assert(False);
            elif (self._IR == (0x4d<<3|1)): assert(False);
            elif (self._IR == (0x4d<<3|2)): assert(False);
            elif (self._IR == (0x4d<<3|3)): assert(False);
            elif (self._IR == (0x4d<<3|4)): assert(False);
            elif (self._IR == (0x4d<<3|5)): assert(False);
            elif (self._IR == (0x4d<<3|6)): assert(False);
            elif (self._IR == (0x4d<<3|7)): assert(False);

            # LSR-a
            elif (self._IR == (0x4e<<3|0)): assert(False);
            elif (self._IR == (0x4e<<3|1)): assert(False);
            elif (self._IR == (0x4e<<3|2)): assert(False);
            elif (self._IR == (0x4e<<3|3)): assert(False);
            elif (self._IR == (0x4e<<3|4)): assert(False);
            elif (self._IR == (0x4e<<3|5)): assert(False);
            elif (self._IR == (0x4e<<3|6)): assert(False);
            elif (self._IR == (0x4e<<3|7)): assert(False);

            # BBR4-r
            elif (self._IR == (0x4f<<3|0)): assert(False);
            elif (self._IR == (0x4f<<3|1)): assert(False);
            elif (self._IR == (0x4f<<3|2)): assert(False);
            elif (self._IR == (0x4f<<3|3)): assert(False);
            elif (self._IR == (0x4f<<3|4)): assert(False);
            elif (self._IR == (0x4f<<3|5)): assert(False);
            elif (self._IR == (0x4f<<3|6)): assert(False);
            elif (self._IR == (0x4f<<3|7)): assert(False);



            # BVC-r
            elif (self._IR == (0x50<<3|0)): assert(False);
            elif (self._IR == (0x50<<3|1)): assert(False);
            elif (self._IR == (0x50<<3|2)): assert(False);
            elif (self._IR == (0x50<<3|3)): assert(False);
            elif (self._IR == (0x50<<3|4)): assert(False);
            elif (self._IR == (0x50<<3|5)): assert(False);
            elif (self._IR == (0x50<<3|6)): assert(False);
            elif (self._IR == (0x50<<3|7)): assert(False);

            # EOR-(zp),y
            elif (self._IR == (0x51<<3|0)): assert(False);
            elif (self._IR == (0x51<<3|1)): assert(False);
            elif (self._IR == (0x51<<3|2)): assert(False);
            elif (self._IR == (0x51<<3|3)): assert(False);
            elif (self._IR == (0x51<<3|4)): assert(False);
            elif (self._IR == (0x51<<3|5)): assert(False);
            elif (self._IR == (0x51<<3|6)): assert(False);
            elif (self._IR == (0x51<<3|7)): assert(False);

            # EOR-(zp)
            elif (self._IR == (0x52<<3|0)): assert(False);
            elif (self._IR == (0x52<<3|1)): assert(False);
            elif (self._IR == (0x52<<3|2)): assert(False);
            elif (self._IR == (0x52<<3|3)): assert(False);
            elif (self._IR == (0x52<<3|4)): assert(False);
            elif (self._IR == (0x52<<3|5)): assert(False);
            elif (self._IR == (0x52<<3|6)): assert(False);
            elif (self._IR == (0x52<<3|7)): assert(False);

            # None
            elif (self._IR == (0x53<<3|0)): assert(False);
            elif (self._IR == (0x53<<3|1)): assert(False);
            elif (self._IR == (0x53<<3|2)): assert(False);
            elif (self._IR == (0x53<<3|3)): assert(False);
            elif (self._IR == (0x53<<3|4)): assert(False);
            elif (self._IR == (0x53<<3|5)): assert(False);
            elif (self._IR == (0x53<<3|6)): assert(False);
            elif (self._IR == (0x53<<3|7)): assert(False);

            # None
            elif (self._IR == (0x54<<3|0)): assert(False);
            elif (self._IR == (0x54<<3|1)): assert(False);
            elif (self._IR == (0x54<<3|2)): assert(False);
            elif (self._IR == (0x54<<3|3)): assert(False);
            elif (self._IR == (0x54<<3|4)): assert(False);
            elif (self._IR == (0x54<<3|5)): assert(False);
            elif (self._IR == (0x54<<3|6)): assert(False);
            elif (self._IR == (0x54<<3|7)): assert(False);

            # EOR-zp,x
            elif (self._IR == (0x55<<3|0)): assert(False);
            elif (self._IR == (0x55<<3|1)): assert(False);
            elif (self._IR == (0x55<<3|2)): assert(False);
            elif (self._IR == (0x55<<3|3)): assert(False);
            elif (self._IR == (0x55<<3|4)): assert(False);
            elif (self._IR == (0x55<<3|5)): assert(False);
            elif (self._IR == (0x55<<3|6)): assert(False);
            elif (self._IR == (0x55<<3|7)): assert(False);

            # LSR-zp,x
            elif (self._IR == (0x56<<3|0)): assert(False);
            elif (self._IR == (0x56<<3|1)): assert(False);
            elif (self._IR == (0x56<<3|2)): assert(False);
            elif (self._IR == (0x56<<3|3)): assert(False);
            elif (self._IR == (0x56<<3|4)): assert(False);
            elif (self._IR == (0x56<<3|5)): assert(False);
            elif (self._IR == (0x56<<3|6)): assert(False);
            elif (self._IR == (0x56<<3|7)): assert(False);

            # RMB5-zp
            elif (self._IR == (0x57<<3|0)): assert(False);
            elif (self._IR == (0x57<<3|1)): assert(False);
            elif (self._IR == (0x57<<3|2)): assert(False);
            elif (self._IR == (0x57<<3|3)): assert(False);
            elif (self._IR == (0x57<<3|4)): assert(False);
            elif (self._IR == (0x57<<3|5)): assert(False);
            elif (self._IR == (0x57<<3|6)): assert(False);
            elif (self._IR == (0x57<<3|7)): assert(False);

            # CLI-i
            elif (self._IR == (0x58<<3|0)): assert(False);
            elif (self._IR == (0x58<<3|1)): assert(False);
            elif (self._IR == (0x58<<3|2)): assert(False);
            elif (self._IR == (0x58<<3|3)): assert(False);
            elif (self._IR == (0x58<<3|4)): assert(False);
            elif (self._IR == (0x58<<3|5)): assert(False);
            elif (self._IR == (0x58<<3|6)): assert(False);
            elif (self._IR == (0x58<<3|7)): assert(False);

            # EOR-a,y
            elif (self._IR == (0x59<<3|0)): assert(False);
            elif (self._IR == (0x59<<3|1)): assert(False);
            elif (self._IR == (0x59<<3|2)): assert(False);
            elif (self._IR == (0x59<<3|3)): assert(False);
            elif (self._IR == (0x59<<3|4)): assert(False);
            elif (self._IR == (0x59<<3|5)): assert(False);
            elif (self._IR == (0x59<<3|6)): assert(False);
            elif (self._IR == (0x59<<3|7)): assert(False);

            # PHY-s
            elif (self._IR == (0x5a<<3|0)): assert(False);
            elif (self._IR == (0x5a<<3|1)): assert(False);
            elif (self._IR == (0x5a<<3|2)): assert(False);
            elif (self._IR == (0x5a<<3|3)): assert(False);
            elif (self._IR == (0x5a<<3|4)): assert(False);
            elif (self._IR == (0x5a<<3|5)): assert(False);
            elif (self._IR == (0x5a<<3|6)): assert(False);
            elif (self._IR == (0x5a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x5b<<3|0)): assert(False);
            elif (self._IR == (0x5b<<3|1)): assert(False);
            elif (self._IR == (0x5b<<3|2)): assert(False);
            elif (self._IR == (0x5b<<3|3)): assert(False);
            elif (self._IR == (0x5b<<3|4)): assert(False);
            elif (self._IR == (0x5b<<3|5)): assert(False);
            elif (self._IR == (0x5b<<3|6)): assert(False);
            elif (self._IR == (0x5b<<3|7)): assert(False);

            # None
            elif (self._IR == (0x5c<<3|0)): assert(False);
            elif (self._IR == (0x5c<<3|1)): assert(False);
            elif (self._IR == (0x5c<<3|2)): assert(False);
            elif (self._IR == (0x5c<<3|3)): assert(False);
            elif (self._IR == (0x5c<<3|4)): assert(False);
            elif (self._IR == (0x5c<<3|5)): assert(False);
            elif (self._IR == (0x5c<<3|6)): assert(False);
            elif (self._IR == (0x5c<<3|7)): assert(False);

            # EOR-a,x
            elif (self._IR == (0x5d<<3|0)): assert(False);
            elif (self._IR == (0x5d<<3|1)): assert(False);
            elif (self._IR == (0x5d<<3|2)): assert(False);
            elif (self._IR == (0x5d<<3|3)): assert(False);
            elif (self._IR == (0x5d<<3|4)): assert(False);
            elif (self._IR == (0x5d<<3|5)): assert(False);
            elif (self._IR == (0x5d<<3|6)): assert(False);
            elif (self._IR == (0x5d<<3|7)): assert(False);

            # LSR-a,x
            elif (self._IR == (0x5e<<3|0)): assert(False);
            elif (self._IR == (0x5e<<3|1)): assert(False);
            elif (self._IR == (0x5e<<3|2)): assert(False);
            elif (self._IR == (0x5e<<3|3)): assert(False);
            elif (self._IR == (0x5e<<3|4)): assert(False);
            elif (self._IR == (0x5e<<3|5)): assert(False);
            elif (self._IR == (0x5e<<3|6)): assert(False);
            elif (self._IR == (0x5e<<3|7)): assert(False);

            # BBR5-r
            elif (self._IR == (0x5f<<3|0)): assert(False);
            elif (self._IR == (0x5f<<3|1)): assert(False);
            elif (self._IR == (0x5f<<3|2)): assert(False);
            elif (self._IR == (0x5f<<3|3)): assert(False);
            elif (self._IR == (0x5f<<3|4)): assert(False);
            elif (self._IR == (0x5f<<3|5)): assert(False);
            elif (self._IR == (0x5f<<3|6)): assert(False);
            elif (self._IR == (0x5f<<3|7)): assert(False);



            # RTS-s
            elif (self._IR == (0x60<<3|0)): assert(False);
            elif (self._IR == (0x60<<3|1)): assert(False);
            elif (self._IR == (0x60<<3|2)): assert(False);
            elif (self._IR == (0x60<<3|3)): assert(False);
            elif (self._IR == (0x60<<3|4)): assert(False);
            elif (self._IR == (0x60<<3|5)): assert(False);
            elif (self._IR == (0x60<<3|6)): assert(False);
            elif (self._IR == (0x60<<3|7)): assert(False);

            # ADC-(zp,x)
            elif (self._IR == (0x61<<3|0)): assert(False);
            elif (self._IR == (0x61<<3|1)): assert(False);
            elif (self._IR == (0x61<<3|2)): assert(False);
            elif (self._IR == (0x61<<3|3)): assert(False);
            elif (self._IR == (0x61<<3|4)): assert(False);
            elif (self._IR == (0x61<<3|5)): assert(False);
            elif (self._IR == (0x61<<3|6)): assert(False);
            elif (self._IR == (0x61<<3|7)): assert(False);

            # None
            elif (self._IR == (0x62<<3|0)): assert(False);
            elif (self._IR == (0x62<<3|1)): assert(False);
            elif (self._IR == (0x62<<3|2)): assert(False);
            elif (self._IR == (0x62<<3|3)): assert(False);
            elif (self._IR == (0x62<<3|4)): assert(False);
            elif (self._IR == (0x62<<3|5)): assert(False);
            elif (self._IR == (0x62<<3|6)): assert(False);
            elif (self._IR == (0x62<<3|7)): assert(False);

            # None
            elif (self._IR == (0x63<<3|0)): assert(False);
            elif (self._IR == (0x63<<3|1)): assert(False);
            elif (self._IR == (0x63<<3|2)): assert(False);
            elif (self._IR == (0x63<<3|3)): assert(False);
            elif (self._IR == (0x63<<3|4)): assert(False);
            elif (self._IR == (0x63<<3|5)): assert(False);
            elif (self._IR == (0x63<<3|6)): assert(False);
            elif (self._IR == (0x63<<3|7)): assert(False);

            # STZ-zp
            elif (self._IR == (0x64<<3|0)): assert(False);
            elif (self._IR == (0x64<<3|1)): assert(False);
            elif (self._IR == (0x64<<3|2)): assert(False);
            elif (self._IR == (0x64<<3|3)): assert(False);
            elif (self._IR == (0x64<<3|4)): assert(False);
            elif (self._IR == (0x64<<3|5)): assert(False);
            elif (self._IR == (0x64<<3|6)): assert(False);
            elif (self._IR == (0x64<<3|7)): assert(False);

            # ADC-zp
            elif (self._IR == (0x65<<3|0)): assert(False);
            elif (self._IR == (0x65<<3|1)): assert(False);
            elif (self._IR == (0x65<<3|2)): assert(False);
            elif (self._IR == (0x65<<3|3)): assert(False);
            elif (self._IR == (0x65<<3|4)): assert(False);
            elif (self._IR == (0x65<<3|5)): assert(False);
            elif (self._IR == (0x65<<3|6)): assert(False);
            elif (self._IR == (0x65<<3|7)): assert(False);

            # ROR-zp
            elif (self._IR == (0x66<<3|0)): assert(False);
            elif (self._IR == (0x66<<3|1)): assert(False);
            elif (self._IR == (0x66<<3|2)): assert(False);
            elif (self._IR == (0x66<<3|3)): assert(False);
            elif (self._IR == (0x66<<3|4)): assert(False);
            elif (self._IR == (0x66<<3|5)): assert(False);
            elif (self._IR == (0x66<<3|6)): assert(False);
            elif (self._IR == (0x66<<3|7)): assert(False);

            # RMB6-zp
            elif (self._IR == (0x67<<3|0)): assert(False);
            elif (self._IR == (0x67<<3|1)): assert(False);
            elif (self._IR == (0x67<<3|2)): assert(False);
            elif (self._IR == (0x67<<3|3)): assert(False);
            elif (self._IR == (0x67<<3|4)): assert(False);
            elif (self._IR == (0x67<<3|5)): assert(False);
            elif (self._IR == (0x67<<3|6)): assert(False);
            elif (self._IR == (0x67<<3|7)): assert(False);

            # PLA-s
            elif (self._IR == (0x68<<3|0)): assert(False);
            elif (self._IR == (0x68<<3|1)): assert(False);
            elif (self._IR == (0x68<<3|2)): assert(False);
            elif (self._IR == (0x68<<3|3)): assert(False);
            elif (self._IR == (0x68<<3|4)): assert(False);
            elif (self._IR == (0x68<<3|5)): assert(False);
            elif (self._IR == (0x68<<3|6)): assert(False);
            elif (self._IR == (0x68<<3|7)): assert(False);

            # ADC-#
            elif (self._IR == (0x69<<3|0)): assert(False);
            elif (self._IR == (0x69<<3|1)): assert(False);
            elif (self._IR == (0x69<<3|2)): assert(False);
            elif (self._IR == (0x69<<3|3)): assert(False);
            elif (self._IR == (0x69<<3|4)): assert(False);
            elif (self._IR == (0x69<<3|5)): assert(False);
            elif (self._IR == (0x69<<3|6)): assert(False);
            elif (self._IR == (0x69<<3|7)): assert(False);

            # ROR-A
            elif (self._IR == (0x6a<<3|0)): assert(False);
            elif (self._IR == (0x6a<<3|1)): assert(False);
            elif (self._IR == (0x6a<<3|2)): assert(False);
            elif (self._IR == (0x6a<<3|3)): assert(False);
            elif (self._IR == (0x6a<<3|4)): assert(False);
            elif (self._IR == (0x6a<<3|5)): assert(False);
            elif (self._IR == (0x6a<<3|6)): assert(False);
            elif (self._IR == (0x6a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x6b<<3|0)): assert(False);
            elif (self._IR == (0x6b<<3|1)): assert(False);
            elif (self._IR == (0x6b<<3|2)): assert(False);
            elif (self._IR == (0x6b<<3|3)): assert(False);
            elif (self._IR == (0x6b<<3|4)): assert(False);
            elif (self._IR == (0x6b<<3|5)): assert(False);
            elif (self._IR == (0x6b<<3|6)): assert(False);
            elif (self._IR == (0x6b<<3|7)): assert(False);

            # JMP-(a)
            elif (self._IR == (0x6c<<3|0)): assert(False);
            elif (self._IR == (0x6c<<3|1)): assert(False);
            elif (self._IR == (0x6c<<3|2)): assert(False);
            elif (self._IR == (0x6c<<3|3)): assert(False);
            elif (self._IR == (0x6c<<3|4)): assert(False);
            elif (self._IR == (0x6c<<3|5)): assert(False);
            elif (self._IR == (0x6c<<3|6)): assert(False);
            elif (self._IR == (0x6c<<3|7)): assert(False);

            # ADC-a
            elif (self._IR == (0x6d<<3|0)): assert(False);
            elif (self._IR == (0x6d<<3|1)): assert(False);
            elif (self._IR == (0x6d<<3|2)): assert(False);
            elif (self._IR == (0x6d<<3|3)): assert(False);
            elif (self._IR == (0x6d<<3|4)): assert(False);
            elif (self._IR == (0x6d<<3|5)): assert(False);
            elif (self._IR == (0x6d<<3|6)): assert(False);
            elif (self._IR == (0x6d<<3|7)): assert(False);

            # ROR-a
            elif (self._IR == (0x6e<<3|0)): assert(False);
            elif (self._IR == (0x6e<<3|1)): assert(False);
            elif (self._IR == (0x6e<<3|2)): assert(False);
            elif (self._IR == (0x6e<<3|3)): assert(False);
            elif (self._IR == (0x6e<<3|4)): assert(False);
            elif (self._IR == (0x6e<<3|5)): assert(False);
            elif (self._IR == (0x6e<<3|6)): assert(False);
            elif (self._IR == (0x6e<<3|7)): assert(False);

            # BBR6-r
            elif (self._IR == (0x6f<<3|0)): assert(False);
            elif (self._IR == (0x6f<<3|1)): assert(False);
            elif (self._IR == (0x6f<<3|2)): assert(False);
            elif (self._IR == (0x6f<<3|3)): assert(False);
            elif (self._IR == (0x6f<<3|4)): assert(False);
            elif (self._IR == (0x6f<<3|5)): assert(False);
            elif (self._IR == (0x6f<<3|6)): assert(False);
            elif (self._IR == (0x6f<<3|7)): assert(False);



            # BVS-r
            elif (self._IR == (0x70<<3|0)): assert(False);
            elif (self._IR == (0x70<<3|1)): assert(False);
            elif (self._IR == (0x70<<3|2)): assert(False);
            elif (self._IR == (0x70<<3|3)): assert(False);
            elif (self._IR == (0x70<<3|4)): assert(False);
            elif (self._IR == (0x70<<3|5)): assert(False);
            elif (self._IR == (0x70<<3|6)): assert(False);
            elif (self._IR == (0x70<<3|7)): assert(False);

            # ADC-(zp),y
            elif (self._IR == (0x71<<3|0)): assert(False);
            elif (self._IR == (0x71<<3|1)): assert(False);
            elif (self._IR == (0x71<<3|2)): assert(False);
            elif (self._IR == (0x71<<3|3)): assert(False);
            elif (self._IR == (0x71<<3|4)): assert(False);
            elif (self._IR == (0x71<<3|5)): assert(False);
            elif (self._IR == (0x71<<3|6)): assert(False);
            elif (self._IR == (0x71<<3|7)): assert(False);

            # ADC-(zp)
            elif (self._IR == (0x72<<3|0)): assert(False);
            elif (self._IR == (0x72<<3|1)): assert(False);
            elif (self._IR == (0x72<<3|2)): assert(False);
            elif (self._IR == (0x72<<3|3)): assert(False);
            elif (self._IR == (0x72<<3|4)): assert(False);
            elif (self._IR == (0x72<<3|5)): assert(False);
            elif (self._IR == (0x72<<3|6)): assert(False);
            elif (self._IR == (0x72<<3|7)): assert(False);

            # None
            elif (self._IR == (0x73<<3|0)): assert(False);
            elif (self._IR == (0x73<<3|1)): assert(False);
            elif (self._IR == (0x73<<3|2)): assert(False);
            elif (self._IR == (0x73<<3|3)): assert(False);
            elif (self._IR == (0x73<<3|4)): assert(False);
            elif (self._IR == (0x73<<3|5)): assert(False);
            elif (self._IR == (0x73<<3|6)): assert(False);
            elif (self._IR == (0x73<<3|7)): assert(False);

            # STZ-zp,x
            elif (self._IR == (0x74<<3|0)): assert(False);
            elif (self._IR == (0x74<<3|1)): assert(False);
            elif (self._IR == (0x74<<3|2)): assert(False);
            elif (self._IR == (0x74<<3|3)): assert(False);
            elif (self._IR == (0x74<<3|4)): assert(False);
            elif (self._IR == (0x74<<3|5)): assert(False);
            elif (self._IR == (0x74<<3|6)): assert(False);
            elif (self._IR == (0x74<<3|7)): assert(False);

            # ADC-zp,x
            elif (self._IR == (0x75<<3|0)): assert(False);
            elif (self._IR == (0x75<<3|1)): assert(False);
            elif (self._IR == (0x75<<3|2)): assert(False);
            elif (self._IR == (0x75<<3|3)): assert(False);
            elif (self._IR == (0x75<<3|4)): assert(False);
            elif (self._IR == (0x75<<3|5)): assert(False);
            elif (self._IR == (0x75<<3|6)): assert(False);
            elif (self._IR == (0x75<<3|7)): assert(False);

            # ROR-zp,x
            elif (self._IR == (0x76<<3|0)): assert(False);
            elif (self._IR == (0x76<<3|1)): assert(False);
            elif (self._IR == (0x76<<3|2)): assert(False);
            elif (self._IR == (0x76<<3|3)): assert(False);
            elif (self._IR == (0x76<<3|4)): assert(False);
            elif (self._IR == (0x76<<3|5)): assert(False);
            elif (self._IR == (0x76<<3|6)): assert(False);
            elif (self._IR == (0x76<<3|7)): assert(False);

            # RMB7-zp
            elif (self._IR == (0x77<<3|0)): assert(False);
            elif (self._IR == (0x77<<3|1)): assert(False);
            elif (self._IR == (0x77<<3|2)): assert(False);
            elif (self._IR == (0x77<<3|3)): assert(False);
            elif (self._IR == (0x77<<3|4)): assert(False);
            elif (self._IR == (0x77<<3|5)): assert(False);
            elif (self._IR == (0x77<<3|6)): assert(False);
            elif (self._IR == (0x77<<3|7)): assert(False);

            # SEI-i
            elif (self._IR == (0x78<<3|0)): assert(False);
            elif (self._IR == (0x78<<3|1)): assert(False);
            elif (self._IR == (0x78<<3|2)): assert(False);
            elif (self._IR == (0x78<<3|3)): assert(False);
            elif (self._IR == (0x78<<3|4)): assert(False);
            elif (self._IR == (0x78<<3|5)): assert(False);
            elif (self._IR == (0x78<<3|6)): assert(False);
            elif (self._IR == (0x78<<3|7)): assert(False);

            # ADC-a,y
            elif (self._IR == (0x79<<3|0)): assert(False);
            elif (self._IR == (0x79<<3|1)): assert(False);
            elif (self._IR == (0x79<<3|2)): assert(False);
            elif (self._IR == (0x79<<3|3)): assert(False);
            elif (self._IR == (0x79<<3|4)): assert(False);
            elif (self._IR == (0x79<<3|5)): assert(False);
            elif (self._IR == (0x79<<3|6)): assert(False);
            elif (self._IR == (0x79<<3|7)): assert(False);

            # PLY-s
            elif (self._IR == (0x7a<<3|0)): assert(False);
            elif (self._IR == (0x7a<<3|1)): assert(False);
            elif (self._IR == (0x7a<<3|2)): assert(False);
            elif (self._IR == (0x7a<<3|3)): assert(False);
            elif (self._IR == (0x7a<<3|4)): assert(False);
            elif (self._IR == (0x7a<<3|5)): assert(False);
            elif (self._IR == (0x7a<<3|6)): assert(False);
            elif (self._IR == (0x7a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x7b<<3|0)): assert(False);
            elif (self._IR == (0x7b<<3|1)): assert(False);
            elif (self._IR == (0x7b<<3|2)): assert(False);
            elif (self._IR == (0x7b<<3|3)): assert(False);
            elif (self._IR == (0x7b<<3|4)): assert(False);
            elif (self._IR == (0x7b<<3|5)): assert(False);
            elif (self._IR == (0x7b<<3|6)): assert(False);
            elif (self._IR == (0x7b<<3|7)): assert(False);

            # JMP-(a,x)
            elif (self._IR == (0x7c<<3|0)): assert(False);
            elif (self._IR == (0x7c<<3|1)): assert(False);
            elif (self._IR == (0x7c<<3|2)): assert(False);
            elif (self._IR == (0x7c<<3|3)): assert(False);
            elif (self._IR == (0x7c<<3|4)): assert(False);
            elif (self._IR == (0x7c<<3|5)): assert(False);
            elif (self._IR == (0x7c<<3|6)): assert(False);
            elif (self._IR == (0x7c<<3|7)): assert(False);

            # ADC-a,x
            elif (self._IR == (0x7d<<3|0)): assert(False);
            elif (self._IR == (0x7d<<3|1)): assert(False);
            elif (self._IR == (0x7d<<3|2)): assert(False);
            elif (self._IR == (0x7d<<3|3)): assert(False);
            elif (self._IR == (0x7d<<3|4)): assert(False);
            elif (self._IR == (0x7d<<3|5)): assert(False);
            elif (self._IR == (0x7d<<3|6)): assert(False);
            elif (self._IR == (0x7d<<3|7)): assert(False);

            # ROR-a,x
            elif (self._IR == (0x7e<<3|0)): assert(False);
            elif (self._IR == (0x7e<<3|1)): assert(False);
            elif (self._IR == (0x7e<<3|2)): assert(False);
            elif (self._IR == (0x7e<<3|3)): assert(False);
            elif (self._IR == (0x7e<<3|4)): assert(False);
            elif (self._IR == (0x7e<<3|5)): assert(False);
            elif (self._IR == (0x7e<<3|6)): assert(False);
            elif (self._IR == (0x7e<<3|7)): assert(False);

            # BBR7-r
            elif (self._IR == (0x7f<<3|0)): assert(False);
            elif (self._IR == (0x7f<<3|1)): assert(False);
            elif (self._IR == (0x7f<<3|2)): assert(False);
            elif (self._IR == (0x7f<<3|3)): assert(False);
            elif (self._IR == (0x7f<<3|4)): assert(False);
            elif (self._IR == (0x7f<<3|5)): assert(False);
            elif (self._IR == (0x7f<<3|6)): assert(False);
            elif (self._IR == (0x7f<<3|7)): assert(False);



            # BRA-r
            elif (self._IR == (0x80<<3|0)): assert(False);
            elif (self._IR == (0x80<<3|1)): assert(False);
            elif (self._IR == (0x80<<3|2)): assert(False);
            elif (self._IR == (0x80<<3|3)): assert(False);
            elif (self._IR == (0x80<<3|4)): assert(False);
            elif (self._IR == (0x80<<3|5)): assert(False);
            elif (self._IR == (0x80<<3|6)): assert(False);
            elif (self._IR == (0x80<<3|7)): assert(False);

            # STA-(zp,x)
            elif (self._IR == (0x81<<3|0)): assert(False);
            elif (self._IR == (0x81<<3|1)): assert(False);
            elif (self._IR == (0x81<<3|2)): assert(False);
            elif (self._IR == (0x81<<3|3)): assert(False);
            elif (self._IR == (0x81<<3|4)): assert(False);
            elif (self._IR == (0x81<<3|5)): assert(False);
            elif (self._IR == (0x81<<3|6)): assert(False);
            elif (self._IR == (0x81<<3|7)): assert(False);

            # None
            elif (self._IR == (0x82<<3|0)): assert(False);
            elif (self._IR == (0x82<<3|1)): assert(False);
            elif (self._IR == (0x82<<3|2)): assert(False);
            elif (self._IR == (0x82<<3|3)): assert(False);
            elif (self._IR == (0x82<<3|4)): assert(False);
            elif (self._IR == (0x82<<3|5)): assert(False);
            elif (self._IR == (0x82<<3|6)): assert(False);
            elif (self._IR == (0x82<<3|7)): assert(False);

            # None
            elif (self._IR == (0x83<<3|0)): assert(False);
            elif (self._IR == (0x83<<3|1)): assert(False);
            elif (self._IR == (0x83<<3|2)): assert(False);
            elif (self._IR == (0x83<<3|3)): assert(False);
            elif (self._IR == (0x83<<3|4)): assert(False);
            elif (self._IR == (0x83<<3|5)): assert(False);
            elif (self._IR == (0x83<<3|6)): assert(False);
            elif (self._IR == (0x83<<3|7)): assert(False);

            # STY-zp
            elif (self._IR == (0x84<<3|0)): assert(False);
            elif (self._IR == (0x84<<3|1)): assert(False);
            elif (self._IR == (0x84<<3|2)): assert(False);
            elif (self._IR == (0x84<<3|3)): assert(False);
            elif (self._IR == (0x84<<3|4)): assert(False);
            elif (self._IR == (0x84<<3|5)): assert(False);
            elif (self._IR == (0x84<<3|6)): assert(False);
            elif (self._IR == (0x84<<3|7)): assert(False);

            # STA-zp
            elif (self._IR == (0x85<<3|0)): assert(False);
            elif (self._IR == (0x85<<3|1)): assert(False);
            elif (self._IR == (0x85<<3|2)): assert(False);
            elif (self._IR == (0x85<<3|3)): assert(False);
            elif (self._IR == (0x85<<3|4)): assert(False);
            elif (self._IR == (0x85<<3|5)): assert(False);
            elif (self._IR == (0x85<<3|6)): assert(False);
            elif (self._IR == (0x85<<3|7)): assert(False);

            # STX-zp
            elif (self._IR == (0x86<<3|0)): assert(False);
            elif (self._IR == (0x86<<3|1)): assert(False);
            elif (self._IR == (0x86<<3|2)): assert(False);
            elif (self._IR == (0x86<<3|3)): assert(False);
            elif (self._IR == (0x86<<3|4)): assert(False);
            elif (self._IR == (0x86<<3|5)): assert(False);
            elif (self._IR == (0x86<<3|6)): assert(False);
            elif (self._IR == (0x86<<3|7)): assert(False);

            # SMB0-zp
            elif (self._IR == (0x87<<3|0)): assert(False);
            elif (self._IR == (0x87<<3|1)): assert(False);
            elif (self._IR == (0x87<<3|2)): assert(False);
            elif (self._IR == (0x87<<3|3)): assert(False);
            elif (self._IR == (0x87<<3|4)): assert(False);
            elif (self._IR == (0x87<<3|5)): assert(False);
            elif (self._IR == (0x87<<3|6)): assert(False);
            elif (self._IR == (0x87<<3|7)): assert(False);

            # DEY-i
            elif (self._IR == (0x88<<3|0)): assert(False);
            elif (self._IR == (0x88<<3|1)): assert(False);
            elif (self._IR == (0x88<<3|2)): assert(False);
            elif (self._IR == (0x88<<3|3)): assert(False);
            elif (self._IR == (0x88<<3|4)): assert(False);
            elif (self._IR == (0x88<<3|5)): assert(False);
            elif (self._IR == (0x88<<3|6)): assert(False);
            elif (self._IR == (0x88<<3|7)): assert(False);

            # BIT-#
            elif (self._IR == (0x89<<3|0)): assert(False);
            elif (self._IR == (0x89<<3|1)): assert(False);
            elif (self._IR == (0x89<<3|2)): assert(False);
            elif (self._IR == (0x89<<3|3)): assert(False);
            elif (self._IR == (0x89<<3|4)): assert(False);
            elif (self._IR == (0x89<<3|5)): assert(False);
            elif (self._IR == (0x89<<3|6)): assert(False);
            elif (self._IR == (0x89<<3|7)): assert(False);

            # TXA-i
            elif (self._IR == (0x8a<<3|0)): assert(False);
            elif (self._IR == (0x8a<<3|1)): assert(False);
            elif (self._IR == (0x8a<<3|2)): assert(False);
            elif (self._IR == (0x8a<<3|3)): assert(False);
            elif (self._IR == (0x8a<<3|4)): assert(False);
            elif (self._IR == (0x8a<<3|5)): assert(False);
            elif (self._IR == (0x8a<<3|6)): assert(False);
            elif (self._IR == (0x8a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x8b<<3|0)): assert(False);
            elif (self._IR == (0x8b<<3|1)): assert(False);
            elif (self._IR == (0x8b<<3|2)): assert(False);
            elif (self._IR == (0x8b<<3|3)): assert(False);
            elif (self._IR == (0x8b<<3|4)): assert(False);
            elif (self._IR == (0x8b<<3|5)): assert(False);
            elif (self._IR == (0x8b<<3|6)): assert(False);
            elif (self._IR == (0x8b<<3|7)): assert(False);

            # STY-a
            elif (self._IR == (0x8c<<3|0)): assert(False);
            elif (self._IR == (0x8c<<3|1)): assert(False);
            elif (self._IR == (0x8c<<3|2)): assert(False);
            elif (self._IR == (0x8c<<3|3)): assert(False);
            elif (self._IR == (0x8c<<3|4)): assert(False);
            elif (self._IR == (0x8c<<3|5)): assert(False);
            elif (self._IR == (0x8c<<3|6)): assert(False);
            elif (self._IR == (0x8c<<3|7)): assert(False);

            # STA-a
            elif (self._IR == (0x8d<<3|0)):  self._SA(self._PC);self._INCPC();
            elif (self._IR == (0x8d<<3|1)): self._SA(self._PC);self._INCPC();self._AD=self._GD();
            elif (self._IR == (0x8d<<3|2)): self._SA((self._GD()<<8)|self._AD);self._SD(self._A);self._WR();
            elif (self._IR == (0x8d<<3|3)): self._FETCH();
            elif (self._IR == (0x8d<<3|4)): assert(False);
            elif (self._IR == (0x8d<<3|5)): assert(False);
            elif (self._IR == (0x8d<<3|6)): assert(False);
            elif (self._IR == (0x8d<<3|7)): assert(False);

            # STX-a
            elif (self._IR == (0x8e<<3|0)): assert(False);
            elif (self._IR == (0x8e<<3|1)): assert(False);
            elif (self._IR == (0x8e<<3|2)): assert(False);
            elif (self._IR == (0x8e<<3|3)): assert(False);
            elif (self._IR == (0x8e<<3|4)): assert(False);
            elif (self._IR == (0x8e<<3|5)): assert(False);
            elif (self._IR == (0x8e<<3|6)): assert(False);
            elif (self._IR == (0x8e<<3|7)): assert(False);

            # BBS0-r
            elif (self._IR == (0x8f<<3|0)): assert(False);
            elif (self._IR == (0x8f<<3|1)): assert(False);
            elif (self._IR == (0x8f<<3|2)): assert(False);
            elif (self._IR == (0x8f<<3|3)): assert(False);
            elif (self._IR == (0x8f<<3|4)): assert(False);
            elif (self._IR == (0x8f<<3|5)): assert(False);
            elif (self._IR == (0x8f<<3|6)): assert(False);
            elif (self._IR == (0x8f<<3|7)): assert(False);



            # BCC-r
            elif (self._IR == (0x90<<3|0)): assert(False);
            elif (self._IR == (0x90<<3|1)): assert(False);
            elif (self._IR == (0x90<<3|2)): assert(False);
            elif (self._IR == (0x90<<3|3)): assert(False);
            elif (self._IR == (0x90<<3|4)): assert(False);
            elif (self._IR == (0x90<<3|5)): assert(False);
            elif (self._IR == (0x90<<3|6)): assert(False);
            elif (self._IR == (0x90<<3|7)): assert(False);

            # STA-(zp),y
            elif (self._IR == (0x91<<3|0)): assert(False);
            elif (self._IR == (0x91<<3|1)): assert(False);
            elif (self._IR == (0x91<<3|2)): assert(False);
            elif (self._IR == (0x91<<3|3)): assert(False);
            elif (self._IR == (0x91<<3|4)): assert(False);
            elif (self._IR == (0x91<<3|5)): assert(False);
            elif (self._IR == (0x91<<3|6)): assert(False);
            elif (self._IR == (0x91<<3|7)): assert(False);

            # STA-(zp)
            elif (self._IR == (0x92<<3|0)): assert(False);
            elif (self._IR == (0x92<<3|1)): assert(False);
            elif (self._IR == (0x92<<3|2)): assert(False);
            elif (self._IR == (0x92<<3|3)): assert(False);
            elif (self._IR == (0x92<<3|4)): assert(False);
            elif (self._IR == (0x92<<3|5)): assert(False);
            elif (self._IR == (0x92<<3|6)): assert(False);
            elif (self._IR == (0x92<<3|7)): assert(False);

            # None
            elif (self._IR == (0x93<<3|0)): assert(False);
            elif (self._IR == (0x93<<3|1)): assert(False);
            elif (self._IR == (0x93<<3|2)): assert(False);
            elif (self._IR == (0x93<<3|3)): assert(False);
            elif (self._IR == (0x93<<3|4)): assert(False);
            elif (self._IR == (0x93<<3|5)): assert(False);
            elif (self._IR == (0x93<<3|6)): assert(False);
            elif (self._IR == (0x93<<3|7)): assert(False);

            # STY-zp,x
            elif (self._IR == (0x94<<3|0)): assert(False);
            elif (self._IR == (0x94<<3|1)): assert(False);
            elif (self._IR == (0x94<<3|2)): assert(False);
            elif (self._IR == (0x94<<3|3)): assert(False);
            elif (self._IR == (0x94<<3|4)): assert(False);
            elif (self._IR == (0x94<<3|5)): assert(False);
            elif (self._IR == (0x94<<3|6)): assert(False);
            elif (self._IR == (0x94<<3|7)): assert(False);

            # STA-zp,x
            elif (self._IR == (0x95<<3|0)): assert(False);
            elif (self._IR == (0x95<<3|1)): assert(False);
            elif (self._IR == (0x95<<3|2)): assert(False);
            elif (self._IR == (0x95<<3|3)): assert(False);
            elif (self._IR == (0x95<<3|4)): assert(False);
            elif (self._IR == (0x95<<3|5)): assert(False);
            elif (self._IR == (0x95<<3|6)): assert(False);
            elif (self._IR == (0x95<<3|7)): assert(False);

            # STX-zp,y
            elif (self._IR == (0x96<<3|0)): assert(False);
            elif (self._IR == (0x96<<3|1)): assert(False);
            elif (self._IR == (0x96<<3|2)): assert(False);
            elif (self._IR == (0x96<<3|3)): assert(False);
            elif (self._IR == (0x96<<3|4)): assert(False);
            elif (self._IR == (0x96<<3|5)): assert(False);
            elif (self._IR == (0x96<<3|6)): assert(False);
            elif (self._IR == (0x96<<3|7)): assert(False);

            # SMB1-zp
            elif (self._IR == (0x97<<3|0)): assert(False);
            elif (self._IR == (0x97<<3|1)): assert(False);
            elif (self._IR == (0x97<<3|2)): assert(False);
            elif (self._IR == (0x97<<3|3)): assert(False);
            elif (self._IR == (0x97<<3|4)): assert(False);
            elif (self._IR == (0x97<<3|5)): assert(False);
            elif (self._IR == (0x97<<3|6)): assert(False);
            elif (self._IR == (0x97<<3|7)): assert(False);

            # TYA-i
            elif (self._IR == (0x98<<3|0)): assert(False);
            elif (self._IR == (0x98<<3|1)): assert(False);
            elif (self._IR == (0x98<<3|2)): assert(False);
            elif (self._IR == (0x98<<3|3)): assert(False);
            elif (self._IR == (0x98<<3|4)): assert(False);
            elif (self._IR == (0x98<<3|5)): assert(False);
            elif (self._IR == (0x98<<3|6)): assert(False);
            elif (self._IR == (0x98<<3|7)): assert(False);

            # STA-a,y
            elif (self._IR == (0x99<<3|0)): assert(False);
            elif (self._IR == (0x99<<3|1)): assert(False);
            elif (self._IR == (0x99<<3|2)): assert(False);
            elif (self._IR == (0x99<<3|3)): assert(False);
            elif (self._IR == (0x99<<3|4)): assert(False);
            elif (self._IR == (0x99<<3|5)): assert(False);
            elif (self._IR == (0x99<<3|6)): assert(False);
            elif (self._IR == (0x99<<3|7)): assert(False);

            # TXS-i
            elif (self._IR == (0x9a<<3|0)): assert(False);
            elif (self._IR == (0x9a<<3|1)): assert(False);
            elif (self._IR == (0x9a<<3|2)): assert(False);
            elif (self._IR == (0x9a<<3|3)): assert(False);
            elif (self._IR == (0x9a<<3|4)): assert(False);
            elif (self._IR == (0x9a<<3|5)): assert(False);
            elif (self._IR == (0x9a<<3|6)): assert(False);
            elif (self._IR == (0x9a<<3|7)): assert(False);

            # None
            elif (self._IR == (0x9b<<3|0)): assert(False);
            elif (self._IR == (0x9b<<3|1)): assert(False);
            elif (self._IR == (0x9b<<3|2)): assert(False);
            elif (self._IR == (0x9b<<3|3)): assert(False);
            elif (self._IR == (0x9b<<3|4)): assert(False);
            elif (self._IR == (0x9b<<3|5)): assert(False);
            elif (self._IR == (0x9b<<3|6)): assert(False);
            elif (self._IR == (0x9b<<3|7)): assert(False);

            # STZ-a
            elif (self._IR == (0x9c<<3|0)): assert(False);
            elif (self._IR == (0x9c<<3|1)): assert(False);
            elif (self._IR == (0x9c<<3|2)): assert(False);
            elif (self._IR == (0x9c<<3|3)): assert(False);
            elif (self._IR == (0x9c<<3|4)): assert(False);
            elif (self._IR == (0x9c<<3|5)): assert(False);
            elif (self._IR == (0x9c<<3|6)): assert(False);
            elif (self._IR == (0x9c<<3|7)): assert(False);

            # STA-a,x
            elif (self._IR == (0x9d<<3|0)): assert(False);
            elif (self._IR == (0x9d<<3|1)): assert(False);
            elif (self._IR == (0x9d<<3|2)): assert(False);
            elif (self._IR == (0x9d<<3|3)): assert(False);
            elif (self._IR == (0x9d<<3|4)): assert(False);
            elif (self._IR == (0x9d<<3|5)): assert(False);
            elif (self._IR == (0x9d<<3|6)): assert(False);
            elif (self._IR == (0x9d<<3|7)): assert(False);

            # STZ-a,x
            elif (self._IR == (0x9e<<3|0)): assert(False);
            elif (self._IR == (0x9e<<3|1)): assert(False);
            elif (self._IR == (0x9e<<3|2)): assert(False);
            elif (self._IR == (0x9e<<3|3)): assert(False);
            elif (self._IR == (0x9e<<3|4)): assert(False);
            elif (self._IR == (0x9e<<3|5)): assert(False);
            elif (self._IR == (0x9e<<3|6)): assert(False);
            elif (self._IR == (0x9e<<3|7)): assert(False);

            # BBS1-r
            elif (self._IR == (0x9f<<3|0)): assert(False);
            elif (self._IR == (0x9f<<3|1)): assert(False);
            elif (self._IR == (0x9f<<3|2)): assert(False);
            elif (self._IR == (0x9f<<3|3)): assert(False);
            elif (self._IR == (0x9f<<3|4)): assert(False);
            elif (self._IR == (0x9f<<3|5)): assert(False);
            elif (self._IR == (0x9f<<3|6)): assert(False);
            elif (self._IR == (0x9f<<3|7)): assert(False);



            # LDY-#
            elif (self._IR == (0xa0<<3|0)): assert(False);
            elif (self._IR == (0xa0<<3|1)): assert(False);
            elif (self._IR == (0xa0<<3|2)): assert(False);
            elif (self._IR == (0xa0<<3|3)): assert(False);
            elif (self._IR == (0xa0<<3|4)): assert(False);
            elif (self._IR == (0xa0<<3|5)): assert(False);
            elif (self._IR == (0xa0<<3|6)): assert(False);
            elif (self._IR == (0xa0<<3|7)): assert(False);

            # LDA-(zp,x)
            elif (self._IR == (0xa1<<3|0)): assert(False);
            elif (self._IR == (0xa1<<3|1)): assert(False);
            elif (self._IR == (0xa1<<3|2)): assert(False);
            elif (self._IR == (0xa1<<3|3)): assert(False);
            elif (self._IR == (0xa1<<3|4)): assert(False);
            elif (self._IR == (0xa1<<3|5)): assert(False);
            elif (self._IR == (0xa1<<3|6)): assert(False);
            elif (self._IR == (0xa1<<3|7)): assert(False);

            # LDX-#
            elif (self._IR == (0xa2<<3|0)): assert(False);
            elif (self._IR == (0xa2<<3|1)): assert(False);
            elif (self._IR == (0xa2<<3|2)): assert(False);
            elif (self._IR == (0xa2<<3|3)): assert(False);
            elif (self._IR == (0xa2<<3|4)): assert(False);
            elif (self._IR == (0xa2<<3|5)): assert(False);
            elif (self._IR == (0xa2<<3|6)): assert(False);
            elif (self._IR == (0xa2<<3|7)): assert(False);

            # None
            elif (self._IR == (0xa3<<3|0)): assert(False);
            elif (self._IR == (0xa3<<3|1)): assert(False);
            elif (self._IR == (0xa3<<3|2)): assert(False);
            elif (self._IR == (0xa3<<3|3)): assert(False);
            elif (self._IR == (0xa3<<3|4)): assert(False);
            elif (self._IR == (0xa3<<3|5)): assert(False);
            elif (self._IR == (0xa3<<3|6)): assert(False);
            elif (self._IR == (0xa3<<3|7)): assert(False);

            # LDY-zp
            elif (self._IR == (0xa4<<3|0)): assert(False);
            elif (self._IR == (0xa4<<3|1)): assert(False);
            elif (self._IR == (0xa4<<3|2)): assert(False);
            elif (self._IR == (0xa4<<3|3)): assert(False);
            elif (self._IR == (0xa4<<3|4)): assert(False);
            elif (self._IR == (0xa4<<3|5)): assert(False);
            elif (self._IR == (0xa4<<3|6)): assert(False);
            elif (self._IR == (0xa4<<3|7)): assert(False);

            # LDA-zp
            elif (self._IR == (0xa5<<3|0)): assert(False);
            elif (self._IR == (0xa5<<3|1)): assert(False);
            elif (self._IR == (0xa5<<3|2)): assert(False);
            elif (self._IR == (0xa5<<3|3)): assert(False);
            elif (self._IR == (0xa5<<3|4)): assert(False);
            elif (self._IR == (0xa5<<3|5)): assert(False);
            elif (self._IR == (0xa5<<3|6)): assert(False);
            elif (self._IR == (0xa5<<3|7)): assert(False);

            # LDX-zp
            elif (self._IR == (0xa6<<3|0)): assert(False);
            elif (self._IR == (0xa6<<3|1)): assert(False);
            elif (self._IR == (0xa6<<3|2)): assert(False);
            elif (self._IR == (0xa6<<3|3)): assert(False);
            elif (self._IR == (0xa6<<3|4)): assert(False);
            elif (self._IR == (0xa6<<3|5)): assert(False);
            elif (self._IR == (0xa6<<3|6)): assert(False);
            elif (self._IR == (0xa6<<3|7)): assert(False);

            # SMB2-zp
            elif (self._IR == (0xa7<<3|0)): assert(False);
            elif (self._IR == (0xa7<<3|1)): assert(False);
            elif (self._IR == (0xa7<<3|2)): assert(False);
            elif (self._IR == (0xa7<<3|3)): assert(False);
            elif (self._IR == (0xa7<<3|4)): assert(False);
            elif (self._IR == (0xa7<<3|5)): assert(False);
            elif (self._IR == (0xa7<<3|6)): assert(False);
            elif (self._IR == (0xa7<<3|7)): assert(False);

            # TAY-i
            elif (self._IR == (0xa8<<3|0)): assert(False);
            elif (self._IR == (0xa8<<3|1)): assert(False);
            elif (self._IR == (0xa8<<3|2)): assert(False);
            elif (self._IR == (0xa8<<3|3)): assert(False);
            elif (self._IR == (0xa8<<3|4)): assert(False);
            elif (self._IR == (0xa8<<3|5)): assert(False);
            elif (self._IR == (0xa8<<3|6)): assert(False);
            elif (self._IR == (0xa8<<3|7)): assert(False);

            # LDA-#
            elif (self._IR == (0xa9<<3|0)): self._SA(self._PC);self._INCPC();
            elif (self._IR == (0xa9<<3|1)): self._A=self._GD();self._NZ(self._A);self._FETCH();
            elif (self._IR == (0xa9<<3|2)): assert(False);
            elif (self._IR == (0xa9<<3|3)): assert(False);
            elif (self._IR == (0xa9<<3|4)): assert(False);
            elif (self._IR == (0xa9<<3|5)): assert(False);
            elif (self._IR == (0xa9<<3|6)): assert(False);
            elif (self._IR == (0xa9<<3|7)): assert(False);

            # TAX-i
            elif (self._IR == (0xaa<<3|0)): assert(False);
            elif (self._IR == (0xaa<<3|1)): assert(False);
            elif (self._IR == (0xaa<<3|2)): assert(False);
            elif (self._IR == (0xaa<<3|3)): assert(False);
            elif (self._IR == (0xaa<<3|4)): assert(False);
            elif (self._IR == (0xaa<<3|5)): assert(False);
            elif (self._IR == (0xaa<<3|6)): assert(False);
            elif (self._IR == (0xaa<<3|7)): assert(False);

            # None
            elif (self._IR == (0xab<<3|0)): assert(False);
            elif (self._IR == (0xab<<3|1)): assert(False);
            elif (self._IR == (0xab<<3|2)): assert(False);
            elif (self._IR == (0xab<<3|3)): assert(False);
            elif (self._IR == (0xab<<3|4)): assert(False);
            elif (self._IR == (0xab<<3|5)): assert(False);
            elif (self._IR == (0xab<<3|6)): assert(False);
            elif (self._IR == (0xab<<3|7)): assert(False);

            # LDY-A
            elif (self._IR == (0xac<<3|0)): assert(False);
            elif (self._IR == (0xac<<3|1)): assert(False);
            elif (self._IR == (0xac<<3|2)): assert(False);
            elif (self._IR == (0xac<<3|3)): assert(False);
            elif (self._IR == (0xac<<3|4)): assert(False);
            elif (self._IR == (0xac<<3|5)): assert(False);
            elif (self._IR == (0xac<<3|6)): assert(False);
            elif (self._IR == (0xac<<3|7)): assert(False);

            # LDA-a
            elif (self._IR == (0xad<<3|0)): assert(False);
            elif (self._IR == (0xad<<3|1)): assert(False);
            elif (self._IR == (0xad<<3|2)): assert(False);
            elif (self._IR == (0xad<<3|3)): assert(False);
            elif (self._IR == (0xad<<3|4)): assert(False);
            elif (self._IR == (0xad<<3|5)): assert(False);
            elif (self._IR == (0xad<<3|6)): assert(False);
            elif (self._IR == (0xad<<3|7)): assert(False);

            # LDX-a
            elif (self._IR == (0xae<<3|0)): assert(False);
            elif (self._IR == (0xae<<3|1)): assert(False);
            elif (self._IR == (0xae<<3|2)): assert(False);
            elif (self._IR == (0xae<<3|3)): assert(False);
            elif (self._IR == (0xae<<3|4)): assert(False);
            elif (self._IR == (0xae<<3|5)): assert(False);
            elif (self._IR == (0xae<<3|6)): assert(False);
            elif (self._IR == (0xae<<3|7)): assert(False);

            # BBS2-r
            elif (self._IR == (0xaf<<3|0)): assert(False);
            elif (self._IR == (0xaf<<3|1)): assert(False);
            elif (self._IR == (0xaf<<3|2)): assert(False);
            elif (self._IR == (0xaf<<3|3)): assert(False);
            elif (self._IR == (0xaf<<3|4)): assert(False);
            elif (self._IR == (0xaf<<3|5)): assert(False);
            elif (self._IR == (0xaf<<3|6)): assert(False);
            elif (self._IR == (0xaf<<3|7)): assert(False);



            # BCS-r
            elif (self._IR == (0xb0<<3|0)): assert(False);
            elif (self._IR == (0xb0<<3|1)): assert(False);
            elif (self._IR == (0xb0<<3|2)): assert(False);
            elif (self._IR == (0xb0<<3|3)): assert(False);
            elif (self._IR == (0xb0<<3|4)): assert(False);
            elif (self._IR == (0xb0<<3|5)): assert(False);
            elif (self._IR == (0xb0<<3|6)): assert(False);
            elif (self._IR == (0xb0<<3|7)): assert(False);

            # LDA-(zp),y
            elif (self._IR == (0xb1<<3|0)): assert(False);
            elif (self._IR == (0xb1<<3|1)): assert(False);
            elif (self._IR == (0xb1<<3|2)): assert(False);
            elif (self._IR == (0xb1<<3|3)): assert(False);
            elif (self._IR == (0xb1<<3|4)): assert(False);
            elif (self._IR == (0xb1<<3|5)): assert(False);
            elif (self._IR == (0xb1<<3|6)): assert(False);
            elif (self._IR == (0xb1<<3|7)): assert(False);

            # LDA-(zp)
            elif (self._IR == (0xb2<<3|0)): assert(False);
            elif (self._IR == (0xb2<<3|1)): assert(False);
            elif (self._IR == (0xb2<<3|2)): assert(False);
            elif (self._IR == (0xb2<<3|3)): assert(False);
            elif (self._IR == (0xb2<<3|4)): assert(False);
            elif (self._IR == (0xb2<<3|5)): assert(False);
            elif (self._IR == (0xb2<<3|6)): assert(False);
            elif (self._IR == (0xb2<<3|7)): assert(False);

            # None
            elif (self._IR == (0xb3<<3|0)): assert(False);
            elif (self._IR == (0xb3<<3|1)): assert(False);
            elif (self._IR == (0xb3<<3|2)): assert(False);
            elif (self._IR == (0xb3<<3|3)): assert(False);
            elif (self._IR == (0xb3<<3|4)): assert(False);
            elif (self._IR == (0xb3<<3|5)): assert(False);
            elif (self._IR == (0xb3<<3|6)): assert(False);
            elif (self._IR == (0xb3<<3|7)): assert(False);

            # LDY-zp,x
            elif (self._IR == (0xb4<<3|0)): assert(False);
            elif (self._IR == (0xb4<<3|1)): assert(False);
            elif (self._IR == (0xb4<<3|2)): assert(False);
            elif (self._IR == (0xb4<<3|3)): assert(False);
            elif (self._IR == (0xb4<<3|4)): assert(False);
            elif (self._IR == (0xb4<<3|5)): assert(False);
            elif (self._IR == (0xb4<<3|6)): assert(False);
            elif (self._IR == (0xb4<<3|7)): assert(False);

            # LDA-zp,x
            elif (self._IR == (0xb5<<3|0)): assert(False);
            elif (self._IR == (0xb5<<3|1)): assert(False);
            elif (self._IR == (0xb5<<3|2)): assert(False);
            elif (self._IR == (0xb5<<3|3)): assert(False);
            elif (self._IR == (0xb5<<3|4)): assert(False);
            elif (self._IR == (0xb5<<3|5)): assert(False);
            elif (self._IR == (0xb5<<3|6)): assert(False);
            elif (self._IR == (0xb5<<3|7)): assert(False);

            # LDX-zp,y
            elif (self._IR == (0xb6<<3|0)): assert(False);
            elif (self._IR == (0xb6<<3|1)): assert(False);
            elif (self._IR == (0xb6<<3|2)): assert(False);
            elif (self._IR == (0xb6<<3|3)): assert(False);
            elif (self._IR == (0xb6<<3|4)): assert(False);
            elif (self._IR == (0xb6<<3|5)): assert(False);
            elif (self._IR == (0xb6<<3|6)): assert(False);
            elif (self._IR == (0xb6<<3|7)): assert(False);

            # SMB3-zp
            elif (self._IR == (0xb7<<3|0)): assert(False);
            elif (self._IR == (0xb7<<3|1)): assert(False);
            elif (self._IR == (0xb7<<3|2)): assert(False);
            elif (self._IR == (0xb7<<3|3)): assert(False);
            elif (self._IR == (0xb7<<3|4)): assert(False);
            elif (self._IR == (0xb7<<3|5)): assert(False);
            elif (self._IR == (0xb7<<3|6)): assert(False);
            elif (self._IR == (0xb7<<3|7)): assert(False);

            # CLV-i
            elif (self._IR == (0xb8<<3|0)): assert(False);
            elif (self._IR == (0xb8<<3|1)): assert(False);
            elif (self._IR == (0xb8<<3|2)): assert(False);
            elif (self._IR == (0xb8<<3|3)): assert(False);
            elif (self._IR == (0xb8<<3|4)): assert(False);
            elif (self._IR == (0xb8<<3|5)): assert(False);
            elif (self._IR == (0xb8<<3|6)): assert(False);
            elif (self._IR == (0xb8<<3|7)): assert(False);

            # LDA-A,y
            elif (self._IR == (0xb9<<3|0)): assert(False);
            elif (self._IR == (0xb9<<3|1)): assert(False);
            elif (self._IR == (0xb9<<3|2)): assert(False);
            elif (self._IR == (0xb9<<3|3)): assert(False);
            elif (self._IR == (0xb9<<3|4)): assert(False);
            elif (self._IR == (0xb9<<3|5)): assert(False);
            elif (self._IR == (0xb9<<3|6)): assert(False);
            elif (self._IR == (0xb9<<3|7)): assert(False);

            # TSX-i
            elif (self._IR == (0xba<<3|0)): assert(False);
            elif (self._IR == (0xba<<3|1)): assert(False);
            elif (self._IR == (0xba<<3|2)): assert(False);
            elif (self._IR == (0xba<<3|3)): assert(False);
            elif (self._IR == (0xba<<3|4)): assert(False);
            elif (self._IR == (0xba<<3|5)): assert(False);
            elif (self._IR == (0xba<<3|6)): assert(False);
            elif (self._IR == (0xba<<3|7)): assert(False);

            # None
            elif (self._IR == (0xbb<<3|0)): assert(False);
            elif (self._IR == (0xbb<<3|1)): assert(False);
            elif (self._IR == (0xbb<<3|2)): assert(False);
            elif (self._IR == (0xbb<<3|3)): assert(False);
            elif (self._IR == (0xbb<<3|4)): assert(False);
            elif (self._IR == (0xbb<<3|5)): assert(False);
            elif (self._IR == (0xbb<<3|6)): assert(False);
            elif (self._IR == (0xbb<<3|7)): assert(False);

            # LDY-a,x
            elif (self._IR == (0xbc<<3|0)): assert(False);
            elif (self._IR == (0xbc<<3|1)): assert(False);
            elif (self._IR == (0xbc<<3|2)): assert(False);
            elif (self._IR == (0xbc<<3|3)): assert(False);
            elif (self._IR == (0xbc<<3|4)): assert(False);
            elif (self._IR == (0xbc<<3|5)): assert(False);
            elif (self._IR == (0xbc<<3|6)): assert(False);
            elif (self._IR == (0xbc<<3|7)): assert(False);

            # LDA-a,x
            elif (self._IR == (0xbd<<3|0)): assert(False);
            elif (self._IR == (0xbd<<3|1)): assert(False);
            elif (self._IR == (0xbd<<3|2)): assert(False);
            elif (self._IR == (0xbd<<3|3)): assert(False);
            elif (self._IR == (0xbd<<3|4)): assert(False);
            elif (self._IR == (0xbd<<3|5)): assert(False);
            elif (self._IR == (0xbd<<3|6)): assert(False);
            elif (self._IR == (0xbd<<3|7)): assert(False);

            # LDX-a,y
            elif (self._IR == (0xbe<<3|0)): assert(False);
            elif (self._IR == (0xbe<<3|1)): assert(False);
            elif (self._IR == (0xbe<<3|2)): assert(False);
            elif (self._IR == (0xbe<<3|3)): assert(False);
            elif (self._IR == (0xbe<<3|4)): assert(False);
            elif (self._IR == (0xbe<<3|5)): assert(False);
            elif (self._IR == (0xbe<<3|6)): assert(False);
            elif (self._IR == (0xbe<<3|7)): assert(False);

            # BBS3-r
            elif (self._IR == (0xbf<<3|0)): assert(False);
            elif (self._IR == (0xbf<<3|1)): assert(False);
            elif (self._IR == (0xbf<<3|2)): assert(False);
            elif (self._IR == (0xbf<<3|3)): assert(False);
            elif (self._IR == (0xbf<<3|4)): assert(False);
            elif (self._IR == (0xbf<<3|5)): assert(False);
            elif (self._IR == (0xbf<<3|6)): assert(False);
            elif (self._IR == (0xbf<<3|7)): assert(False);



            # CPY-#
            elif (self._IR == (0xc0<<3|0)): assert(False);
            elif (self._IR == (0xc0<<3|1)): assert(False);
            elif (self._IR == (0xc0<<3|2)): assert(False);
            elif (self._IR == (0xc0<<3|3)): assert(False);
            elif (self._IR == (0xc0<<3|4)): assert(False);
            elif (self._IR == (0xc0<<3|5)): assert(False);
            elif (self._IR == (0xc0<<3|6)): assert(False);
            elif (self._IR == (0xc0<<3|7)): assert(False);

            # CMP-(zp,x)
            elif (self._IR == (0xc1<<3|0)): assert(False);
            elif (self._IR == (0xc1<<3|1)): assert(False);
            elif (self._IR == (0xc1<<3|2)): assert(False);
            elif (self._IR == (0xc1<<3|3)): assert(False);
            elif (self._IR == (0xc1<<3|4)): assert(False);
            elif (self._IR == (0xc1<<3|5)): assert(False);
            elif (self._IR == (0xc1<<3|6)): assert(False);
            elif (self._IR == (0xc1<<3|7)): assert(False);

            # None
            elif (self._IR == (0xc2<<3|0)): assert(False);
            elif (self._IR == (0xc2<<3|1)): assert(False);
            elif (self._IR == (0xc2<<3|2)): assert(False);
            elif (self._IR == (0xc2<<3|3)): assert(False);
            elif (self._IR == (0xc2<<3|4)): assert(False);
            elif (self._IR == (0xc2<<3|5)): assert(False);
            elif (self._IR == (0xc2<<3|6)): assert(False);
            elif (self._IR == (0xc2<<3|7)): assert(False);

            # None
            elif (self._IR == (0xc3<<3|0)): assert(False);
            elif (self._IR == (0xc3<<3|1)): assert(False);
            elif (self._IR == (0xc3<<3|2)): assert(False);
            elif (self._IR == (0xc3<<3|3)): assert(False);
            elif (self._IR == (0xc3<<3|4)): assert(False);
            elif (self._IR == (0xc3<<3|5)): assert(False);
            elif (self._IR == (0xc3<<3|6)): assert(False);
            elif (self._IR == (0xc3<<3|7)): assert(False);

            # CPY-zp
            elif (self._IR == (0xc4<<3|0)): assert(False);
            elif (self._IR == (0xc4<<3|1)): assert(False);
            elif (self._IR == (0xc4<<3|2)): assert(False);
            elif (self._IR == (0xc4<<3|3)): assert(False);
            elif (self._IR == (0xc4<<3|4)): assert(False);
            elif (self._IR == (0xc4<<3|5)): assert(False);
            elif (self._IR == (0xc4<<3|6)): assert(False);
            elif (self._IR == (0xc4<<3|7)): assert(False);

            # CMP-zp
            elif (self._IR == (0xc5<<3|0)): assert(False);
            elif (self._IR == (0xc5<<3|1)): assert(False);
            elif (self._IR == (0xc5<<3|2)): assert(False);
            elif (self._IR == (0xc5<<3|3)): assert(False);
            elif (self._IR == (0xc5<<3|4)): assert(False);
            elif (self._IR == (0xc5<<3|5)): assert(False);
            elif (self._IR == (0xc5<<3|6)): assert(False);
            elif (self._IR == (0xc5<<3|7)): assert(False);

            # DEC-zp
            elif (self._IR == (0xc6<<3|0)): assert(False);
            elif (self._IR == (0xc6<<3|1)): assert(False);
            elif (self._IR == (0xc6<<3|2)): assert(False);
            elif (self._IR == (0xc6<<3|3)): assert(False);
            elif (self._IR == (0xc6<<3|4)): assert(False);
            elif (self._IR == (0xc6<<3|5)): assert(False);
            elif (self._IR == (0xc6<<3|6)): assert(False);
            elif (self._IR == (0xc6<<3|7)): assert(False);

            # SMB4-zp
            elif (self._IR == (0xc7<<3|0)): assert(False);
            elif (self._IR == (0xc7<<3|1)): assert(False);
            elif (self._IR == (0xc7<<3|2)): assert(False);
            elif (self._IR == (0xc7<<3|3)): assert(False);
            elif (self._IR == (0xc7<<3|4)): assert(False);
            elif (self._IR == (0xc7<<3|5)): assert(False);
            elif (self._IR == (0xc7<<3|6)): assert(False);
            elif (self._IR == (0xc7<<3|7)): assert(False);

            # INY-i
            elif (self._IR == (0xc8<<3|0)): assert(False);
            elif (self._IR == (0xc8<<3|1)): assert(False);
            elif (self._IR == (0xc8<<3|2)): assert(False);
            elif (self._IR == (0xc8<<3|3)): assert(False);
            elif (self._IR == (0xc8<<3|4)): assert(False);
            elif (self._IR == (0xc8<<3|5)): assert(False);
            elif (self._IR == (0xc8<<3|6)): assert(False);
            elif (self._IR == (0xc8<<3|7)): assert(False);

            # CMP-#
            elif (self._IR == (0xc9<<3|0)): assert(False);
            elif (self._IR == (0xc9<<3|1)): assert(False);
            elif (self._IR == (0xc9<<3|2)): assert(False);
            elif (self._IR == (0xc9<<3|3)): assert(False);
            elif (self._IR == (0xc9<<3|4)): assert(False);
            elif (self._IR == (0xc9<<3|5)): assert(False);
            elif (self._IR == (0xc9<<3|6)): assert(False);
            elif (self._IR == (0xc9<<3|7)): assert(False);

            # DEX-i
            elif (self._IR == (0xca<<3|0)): assert(False);
            elif (self._IR == (0xca<<3|1)): assert(False);
            elif (self._IR == (0xca<<3|2)): assert(False);
            elif (self._IR == (0xca<<3|3)): assert(False);
            elif (self._IR == (0xca<<3|4)): assert(False);
            elif (self._IR == (0xca<<3|5)): assert(False);
            elif (self._IR == (0xca<<3|6)): assert(False);
            elif (self._IR == (0xca<<3|7)): assert(False);

            # WAI-I
            elif (self._IR == (0xcb<<3|0)): assert(False);
            elif (self._IR == (0xcb<<3|1)): assert(False);
            elif (self._IR == (0xcb<<3|2)): assert(False);
            elif (self._IR == (0xcb<<3|3)): assert(False);
            elif (self._IR == (0xcb<<3|4)): assert(False);
            elif (self._IR == (0xcb<<3|5)): assert(False);
            elif (self._IR == (0xcb<<3|6)): assert(False);
            elif (self._IR == (0xcb<<3|7)): assert(False);

            # CPY-a
            elif (self._IR == (0xcc<<3|0)): assert(False);
            elif (self._IR == (0xcc<<3|1)): assert(False);
            elif (self._IR == (0xcc<<3|2)): assert(False);
            elif (self._IR == (0xcc<<3|3)): assert(False);
            elif (self._IR == (0xcc<<3|4)): assert(False);
            elif (self._IR == (0xcc<<3|5)): assert(False);
            elif (self._IR == (0xcc<<3|6)): assert(False);
            elif (self._IR == (0xcc<<3|7)): assert(False);

            # CMP-a
            elif (self._IR == (0xcd<<3|0)): assert(False);
            elif (self._IR == (0xcd<<3|1)): assert(False);
            elif (self._IR == (0xcd<<3|2)): assert(False);
            elif (self._IR == (0xcd<<3|3)): assert(False);
            elif (self._IR == (0xcd<<3|4)): assert(False);
            elif (self._IR == (0xcd<<3|5)): assert(False);
            elif (self._IR == (0xcd<<3|6)): assert(False);
            elif (self._IR == (0xcd<<3|7)): assert(False);

            # DEC-a
            elif (self._IR == (0xce<<3|0)): assert(False);
            elif (self._IR == (0xce<<3|1)): assert(False);
            elif (self._IR == (0xce<<3|2)): assert(False);
            elif (self._IR == (0xce<<3|3)): assert(False);
            elif (self._IR == (0xce<<3|4)): assert(False);
            elif (self._IR == (0xce<<3|5)): assert(False);
            elif (self._IR == (0xce<<3|6)): assert(False);
            elif (self._IR == (0xce<<3|7)): assert(False);

            # BBS4-r
            elif (self._IR == (0xcf<<3|0)): assert(False);
            elif (self._IR == (0xcf<<3|1)): assert(False);
            elif (self._IR == (0xcf<<3|2)): assert(False);
            elif (self._IR == (0xcf<<3|3)): assert(False);
            elif (self._IR == (0xcf<<3|4)): assert(False);
            elif (self._IR == (0xcf<<3|5)): assert(False);
            elif (self._IR == (0xcf<<3|6)): assert(False);
            elif (self._IR == (0xcf<<3|7)): assert(False);



            # BNE-r
            elif (self._IR == (0xd0<<3|0)): assert(False);
            elif (self._IR == (0xd0<<3|1)): assert(False);
            elif (self._IR == (0xd0<<3|2)): assert(False);
            elif (self._IR == (0xd0<<3|3)): assert(False);
            elif (self._IR == (0xd0<<3|4)): assert(False);
            elif (self._IR == (0xd0<<3|5)): assert(False);
            elif (self._IR == (0xd0<<3|6)): assert(False);
            elif (self._IR == (0xd0<<3|7)): assert(False);

            # CMP-(zp),y
            elif (self._IR == (0xd1<<3|0)): assert(False);
            elif (self._IR == (0xd1<<3|1)): assert(False);
            elif (self._IR == (0xd1<<3|2)): assert(False);
            elif (self._IR == (0xd1<<3|3)): assert(False);
            elif (self._IR == (0xd1<<3|4)): assert(False);
            elif (self._IR == (0xd1<<3|5)): assert(False);
            elif (self._IR == (0xd1<<3|6)): assert(False);
            elif (self._IR == (0xd1<<3|7)): assert(False);

            # CMP-(zp)
            elif (self._IR == (0xd2<<3|0)): assert(False);
            elif (self._IR == (0xd2<<3|1)): assert(False);
            elif (self._IR == (0xd2<<3|2)): assert(False);
            elif (self._IR == (0xd2<<3|3)): assert(False);
            elif (self._IR == (0xd2<<3|4)): assert(False);
            elif (self._IR == (0xd2<<3|5)): assert(False);
            elif (self._IR == (0xd2<<3|6)): assert(False);
            elif (self._IR == (0xd2<<3|7)): assert(False);

            # None
            elif (self._IR == (0xd3<<3|0)): assert(False);
            elif (self._IR == (0xd3<<3|1)): assert(False);
            elif (self._IR == (0xd3<<3|2)): assert(False);
            elif (self._IR == (0xd3<<3|3)): assert(False);
            elif (self._IR == (0xd3<<3|4)): assert(False);
            elif (self._IR == (0xd3<<3|5)): assert(False);
            elif (self._IR == (0xd3<<3|6)): assert(False);
            elif (self._IR == (0xd3<<3|7)): assert(False);

            # None
            elif (self._IR == (0xd4<<3|0)): assert(False);
            elif (self._IR == (0xd4<<3|1)): assert(False);
            elif (self._IR == (0xd4<<3|2)): assert(False);
            elif (self._IR == (0xd4<<3|3)): assert(False);
            elif (self._IR == (0xd4<<3|4)): assert(False);
            elif (self._IR == (0xd4<<3|5)): assert(False);
            elif (self._IR == (0xd4<<3|6)): assert(False);
            elif (self._IR == (0xd4<<3|7)): assert(False);

            # CMP-zp,x
            elif (self._IR == (0xd5<<3|0)): assert(False);
            elif (self._IR == (0xd5<<3|1)): assert(False);
            elif (self._IR == (0xd5<<3|2)): assert(False);
            elif (self._IR == (0xd5<<3|3)): assert(False);
            elif (self._IR == (0xd5<<3|4)): assert(False);
            elif (self._IR == (0xd5<<3|5)): assert(False);
            elif (self._IR == (0xd5<<3|6)): assert(False);
            elif (self._IR == (0xd5<<3|7)): assert(False);

            # DEC-zp,x
            elif (self._IR == (0xd6<<3|0)): assert(False);
            elif (self._IR == (0xd6<<3|1)): assert(False);
            elif (self._IR == (0xd6<<3|2)): assert(False);
            elif (self._IR == (0xd6<<3|3)): assert(False);
            elif (self._IR == (0xd6<<3|4)): assert(False);
            elif (self._IR == (0xd6<<3|5)): assert(False);
            elif (self._IR == (0xd6<<3|6)): assert(False);
            elif (self._IR == (0xd6<<3|7)): assert(False);

            # SMB5-zp
            elif (self._IR == (0xd7<<3|0)): assert(False);
            elif (self._IR == (0xd7<<3|1)): assert(False);
            elif (self._IR == (0xd7<<3|2)): assert(False);
            elif (self._IR == (0xd7<<3|3)): assert(False);
            elif (self._IR == (0xd7<<3|4)): assert(False);
            elif (self._IR == (0xd7<<3|5)): assert(False);
            elif (self._IR == (0xd7<<3|6)): assert(False);
            elif (self._IR == (0xd7<<3|7)): assert(False);

            # CLD-i
            elif (self._IR == (0xd8<<3|0)): assert(False);
            elif (self._IR == (0xd8<<3|1)): assert(False);
            elif (self._IR == (0xd8<<3|2)): assert(False);
            elif (self._IR == (0xd8<<3|3)): assert(False);
            elif (self._IR == (0xd8<<3|4)): assert(False);
            elif (self._IR == (0xd8<<3|5)): assert(False);
            elif (self._IR == (0xd8<<3|6)): assert(False);
            elif (self._IR == (0xd8<<3|7)): assert(False);

            # CMP-a,y
            elif (self._IR == (0xd9<<3|0)): assert(False);
            elif (self._IR == (0xd9<<3|1)): assert(False);
            elif (self._IR == (0xd9<<3|2)): assert(False);
            elif (self._IR == (0xd9<<3|3)): assert(False);
            elif (self._IR == (0xd9<<3|4)): assert(False);
            elif (self._IR == (0xd9<<3|5)): assert(False);
            elif (self._IR == (0xd9<<3|6)): assert(False);
            elif (self._IR == (0xd9<<3|7)): assert(False);

            # PHX-s
            elif (self._IR == (0xda<<3|0)): assert(False);
            elif (self._IR == (0xda<<3|1)): assert(False);
            elif (self._IR == (0xda<<3|2)): assert(False);
            elif (self._IR == (0xda<<3|3)): assert(False);
            elif (self._IR == (0xda<<3|4)): assert(False);
            elif (self._IR == (0xda<<3|5)): assert(False);
            elif (self._IR == (0xda<<3|6)): assert(False);
            elif (self._IR == (0xda<<3|7)): assert(False);

            # STP-I
            elif (self._IR == (0xdb<<3|0)): assert(False);
            elif (self._IR == (0xdb<<3|1)): assert(False);
            elif (self._IR == (0xdb<<3|2)): assert(False);
            elif (self._IR == (0xdb<<3|3)): assert(False);
            elif (self._IR == (0xdb<<3|4)): assert(False);
            elif (self._IR == (0xdb<<3|5)): assert(False);
            elif (self._IR == (0xdb<<3|6)): assert(False);
            elif (self._IR == (0xdb<<3|7)): assert(False);

            # None
            elif (self._IR == (0xdc<<3|0)): assert(False);
            elif (self._IR == (0xdc<<3|1)): assert(False);
            elif (self._IR == (0xdc<<3|2)): assert(False);
            elif (self._IR == (0xdc<<3|3)): assert(False);
            elif (self._IR == (0xdc<<3|4)): assert(False);
            elif (self._IR == (0xdc<<3|5)): assert(False);
            elif (self._IR == (0xdc<<3|6)): assert(False);
            elif (self._IR == (0xdc<<3|7)): assert(False);

            # CMP-a,x
            elif (self._IR == (0xdd<<3|0)): assert(False);
            elif (self._IR == (0xdd<<3|1)): assert(False);
            elif (self._IR == (0xdd<<3|2)): assert(False);
            elif (self._IR == (0xdd<<3|3)): assert(False);
            elif (self._IR == (0xdd<<3|4)): assert(False);
            elif (self._IR == (0xdd<<3|5)): assert(False);
            elif (self._IR == (0xdd<<3|6)): assert(False);
            elif (self._IR == (0xdd<<3|7)): assert(False);

            # DEC-a,x
            elif (self._IR == (0xde<<3|0)): assert(False);
            elif (self._IR == (0xde<<3|1)): assert(False);
            elif (self._IR == (0xde<<3|2)): assert(False);
            elif (self._IR == (0xde<<3|3)): assert(False);
            elif (self._IR == (0xde<<3|4)): assert(False);
            elif (self._IR == (0xde<<3|5)): assert(False);
            elif (self._IR == (0xde<<3|6)): assert(False);
            elif (self._IR == (0xde<<3|7)): assert(False);

            # BBS5-r
            elif (self._IR == (0xdf<<3|0)): assert(False);
            elif (self._IR == (0xdf<<3|1)): assert(False);
            elif (self._IR == (0xdf<<3|2)): assert(False);
            elif (self._IR == (0xdf<<3|3)): assert(False);
            elif (self._IR == (0xdf<<3|4)): assert(False);
            elif (self._IR == (0xdf<<3|5)): assert(False);
            elif (self._IR == (0xdf<<3|6)): assert(False);
            elif (self._IR == (0xdf<<3|7)): assert(False);



            # CPX-#
            elif (self._IR == (0xe0<<3|0)): assert(False);
            elif (self._IR == (0xe0<<3|1)): assert(False);
            elif (self._IR == (0xe0<<3|2)): assert(False);
            elif (self._IR == (0xe0<<3|3)): assert(False);
            elif (self._IR == (0xe0<<3|4)): assert(False);
            elif (self._IR == (0xe0<<3|5)): assert(False);
            elif (self._IR == (0xe0<<3|6)): assert(False);
            elif (self._IR == (0xe0<<3|7)): assert(False);

            # SBC-(zp,x)
            elif (self._IR == (0xe1<<3|0)): assert(False);
            elif (self._IR == (0xe1<<3|1)): assert(False);
            elif (self._IR == (0xe1<<3|2)): assert(False);
            elif (self._IR == (0xe1<<3|3)): assert(False);
            elif (self._IR == (0xe1<<3|4)): assert(False);
            elif (self._IR == (0xe1<<3|5)): assert(False);
            elif (self._IR == (0xe1<<3|6)): assert(False);
            elif (self._IR == (0xe1<<3|7)): assert(False);

            # None
            elif (self._IR == (0xe2<<3|0)): assert(False);
            elif (self._IR == (0xe2<<3|1)): assert(False);
            elif (self._IR == (0xe2<<3|2)): assert(False);
            elif (self._IR == (0xe2<<3|3)): assert(False);
            elif (self._IR == (0xe2<<3|4)): assert(False);
            elif (self._IR == (0xe2<<3|5)): assert(False);
            elif (self._IR == (0xe2<<3|6)): assert(False);
            elif (self._IR == (0xe2<<3|7)): assert(False);

            # None
            elif (self._IR == (0xe3<<3|0)): assert(False);
            elif (self._IR == (0xe3<<3|1)): assert(False);
            elif (self._IR == (0xe3<<3|2)): assert(False);
            elif (self._IR == (0xe3<<3|3)): assert(False);
            elif (self._IR == (0xe3<<3|4)): assert(False);
            elif (self._IR == (0xe3<<3|5)): assert(False);
            elif (self._IR == (0xe3<<3|6)): assert(False);
            elif (self._IR == (0xe3<<3|7)): assert(False);

            # CPX-zp
            elif (self._IR == (0xe4<<3|0)): assert(False);
            elif (self._IR == (0xe4<<3|1)): assert(False);
            elif (self._IR == (0xe4<<3|2)): assert(False);
            elif (self._IR == (0xe4<<3|3)): assert(False);
            elif (self._IR == (0xe4<<3|4)): assert(False);
            elif (self._IR == (0xe4<<3|5)): assert(False);
            elif (self._IR == (0xe4<<3|6)): assert(False);
            elif (self._IR == (0xe4<<3|7)): assert(False);

            # SBC-zp
            elif (self._IR == (0xe5<<3|0)): assert(False);
            elif (self._IR == (0xe5<<3|1)): assert(False);
            elif (self._IR == (0xe5<<3|2)): assert(False);
            elif (self._IR == (0xe5<<3|3)): assert(False);
            elif (self._IR == (0xe5<<3|4)): assert(False);
            elif (self._IR == (0xe5<<3|5)): assert(False);
            elif (self._IR == (0xe5<<3|6)): assert(False);
            elif (self._IR == (0xe5<<3|7)): assert(False);

            # INC-zp
            elif (self._IR == (0xe6<<3|0)): assert(False);
            elif (self._IR == (0xe6<<3|1)): assert(False);
            elif (self._IR == (0xe6<<3|2)): assert(False);
            elif (self._IR == (0xe6<<3|3)): assert(False);
            elif (self._IR == (0xe6<<3|4)): assert(False);
            elif (self._IR == (0xe6<<3|5)): assert(False);
            elif (self._IR == (0xe6<<3|6)): assert(False);
            elif (self._IR == (0xe6<<3|7)): assert(False);

            # SMB6-zp
            elif (self._IR == (0xe7<<3|0)): assert(False);
            elif (self._IR == (0xe7<<3|1)): assert(False);
            elif (self._IR == (0xe7<<3|2)): assert(False);
            elif (self._IR == (0xe7<<3|3)): assert(False);
            elif (self._IR == (0xe7<<3|4)): assert(False);
            elif (self._IR == (0xe7<<3|5)): assert(False);
            elif (self._IR == (0xe7<<3|6)): assert(False);
            elif (self._IR == (0xe7<<3|7)): assert(False);

            # INX-i
            elif (self._IR == (0xe8<<3|0)): assert(False);
            elif (self._IR == (0xe8<<3|1)): assert(False);
            elif (self._IR == (0xe8<<3|2)): assert(False);
            elif (self._IR == (0xe8<<3|3)): assert(False);
            elif (self._IR == (0xe8<<3|4)): assert(False);
            elif (self._IR == (0xe8<<3|5)): assert(False);
            elif (self._IR == (0xe8<<3|6)): assert(False);
            elif (self._IR == (0xe8<<3|7)): assert(False);

            # SBC-#
            elif (self._IR == (0xe9<<3|0)): assert(False);
            elif (self._IR == (0xe9<<3|1)): assert(False);
            elif (self._IR == (0xe9<<3|2)): assert(False);
            elif (self._IR == (0xe9<<3|3)): assert(False);
            elif (self._IR == (0xe9<<3|4)): assert(False);
            elif (self._IR == (0xe9<<3|5)): assert(False);
            elif (self._IR == (0xe9<<3|6)): assert(False);
            elif (self._IR == (0xe9<<3|7)): assert(False);

            # NOP-i
            elif (self._IR == (0xea<<3|0)): assert(False);
            elif (self._IR == (0xea<<3|1)): assert(False);
            elif (self._IR == (0xea<<3|2)): assert(False);
            elif (self._IR == (0xea<<3|3)): assert(False);
            elif (self._IR == (0xea<<3|4)): assert(False);
            elif (self._IR == (0xea<<3|5)): assert(False);
            elif (self._IR == (0xea<<3|6)): assert(False);
            elif (self._IR == (0xea<<3|7)): assert(False);

            # None
            elif (self._IR == (0xeb<<3|0)): assert(False);
            elif (self._IR == (0xeb<<3|1)): assert(False);
            elif (self._IR == (0xeb<<3|2)): assert(False);
            elif (self._IR == (0xeb<<3|3)): assert(False);
            elif (self._IR == (0xeb<<3|4)): assert(False);
            elif (self._IR == (0xeb<<3|5)): assert(False);
            elif (self._IR == (0xeb<<3|6)): assert(False);
            elif (self._IR == (0xeb<<3|7)): assert(False);

            # CPX-a
            elif (self._IR == (0xec<<3|0)): assert(False);
            elif (self._IR == (0xec<<3|1)): assert(False);
            elif (self._IR == (0xec<<3|2)): assert(False);
            elif (self._IR == (0xec<<3|3)): assert(False);
            elif (self._IR == (0xec<<3|4)): assert(False);
            elif (self._IR == (0xec<<3|5)): assert(False);
            elif (self._IR == (0xec<<3|6)): assert(False);
            elif (self._IR == (0xec<<3|7)): assert(False);

            # SBC-a
            elif (self._IR == (0xed<<3|0)): assert(False);
            elif (self._IR == (0xed<<3|1)): assert(False);
            elif (self._IR == (0xed<<3|2)): assert(False);
            elif (self._IR == (0xed<<3|3)): assert(False);
            elif (self._IR == (0xed<<3|4)): assert(False);
            elif (self._IR == (0xed<<3|5)): assert(False);
            elif (self._IR == (0xed<<3|6)): assert(False);
            elif (self._IR == (0xed<<3|7)): assert(False);

            # INC-a
            elif (self._IR == (0xee<<3|0)): assert(False);
            elif (self._IR == (0xee<<3|1)): assert(False);
            elif (self._IR == (0xee<<3|2)): assert(False);
            elif (self._IR == (0xee<<3|3)): assert(False);
            elif (self._IR == (0xee<<3|4)): assert(False);
            elif (self._IR == (0xee<<3|5)): assert(False);
            elif (self._IR == (0xee<<3|6)): assert(False);
            elif (self._IR == (0xee<<3|7)): assert(False);

            # BBS6-r
            elif (self._IR == (0xef<<3|0)): assert(False);
            elif (self._IR == (0xef<<3|1)): assert(False);
            elif (self._IR == (0xef<<3|2)): assert(False);
            elif (self._IR == (0xef<<3|3)): assert(False);
            elif (self._IR == (0xef<<3|4)): assert(False);
            elif (self._IR == (0xef<<3|5)): assert(False);
            elif (self._IR == (0xef<<3|6)): assert(False);
            elif (self._IR == (0xef<<3|7)): assert(False);



            # BEQ-r
            elif (self._IR == (0xf0<<3|0)): assert(False);
            elif (self._IR == (0xf0<<3|1)): assert(False);
            elif (self._IR == (0xf0<<3|2)): assert(False);
            elif (self._IR == (0xf0<<3|3)): assert(False);
            elif (self._IR == (0xf0<<3|4)): assert(False);
            elif (self._IR == (0xf0<<3|5)): assert(False);
            elif (self._IR == (0xf0<<3|6)): assert(False);
            elif (self._IR == (0xf0<<3|7)): assert(False);

            # SBC-(zp),y
            elif (self._IR == (0xf1<<3|0)): assert(False);
            elif (self._IR == (0xf1<<3|1)): assert(False);
            elif (self._IR == (0xf1<<3|2)): assert(False);
            elif (self._IR == (0xf1<<3|3)): assert(False);
            elif (self._IR == (0xf1<<3|4)): assert(False);
            elif (self._IR == (0xf1<<3|5)): assert(False);
            elif (self._IR == (0xf1<<3|6)): assert(False);
            elif (self._IR == (0xf1<<3|7)): assert(False);

            # SBC-(zp)
            elif (self._IR == (0xf2<<3|0)): assert(False);
            elif (self._IR == (0xf2<<3|1)): assert(False);
            elif (self._IR == (0xf2<<3|2)): assert(False);
            elif (self._IR == (0xf2<<3|3)): assert(False);
            elif (self._IR == (0xf2<<3|4)): assert(False);
            elif (self._IR == (0xf2<<3|5)): assert(False);
            elif (self._IR == (0xf2<<3|6)): assert(False);
            elif (self._IR == (0xf2<<3|7)): assert(False);

            # None
            elif (self._IR == (0xf3<<3|0)): assert(False);
            elif (self._IR == (0xf3<<3|1)): assert(False);
            elif (self._IR == (0xf3<<3|2)): assert(False);
            elif (self._IR == (0xf3<<3|3)): assert(False);
            elif (self._IR == (0xf3<<3|4)): assert(False);
            elif (self._IR == (0xf3<<3|5)): assert(False);
            elif (self._IR == (0xf3<<3|6)): assert(False);
            elif (self._IR == (0xf3<<3|7)): assert(False);

            # None
            elif (self._IR == (0xf4<<3|0)): assert(False);
            elif (self._IR == (0xf4<<3|1)): assert(False);
            elif (self._IR == (0xf4<<3|2)): assert(False);
            elif (self._IR == (0xf4<<3|3)): assert(False);
            elif (self._IR == (0xf4<<3|4)): assert(False);
            elif (self._IR == (0xf4<<3|5)): assert(False);
            elif (self._IR == (0xf4<<3|6)): assert(False);
            elif (self._IR == (0xf4<<3|7)): assert(False);

            # SBC-zp,x
            elif (self._IR == (0xf5<<3|0)): assert(False);
            elif (self._IR == (0xf5<<3|1)): assert(False);
            elif (self._IR == (0xf5<<3|2)): assert(False);
            elif (self._IR == (0xf5<<3|3)): assert(False);
            elif (self._IR == (0xf5<<3|4)): assert(False);
            elif (self._IR == (0xf5<<3|5)): assert(False);
            elif (self._IR == (0xf5<<3|6)): assert(False);
            elif (self._IR == (0xf5<<3|7)): assert(False);

            # INC-zp,x
            elif (self._IR == (0xf6<<3|0)): assert(False);
            elif (self._IR == (0xf6<<3|1)): assert(False);
            elif (self._IR == (0xf6<<3|2)): assert(False);
            elif (self._IR == (0xf6<<3|3)): assert(False);
            elif (self._IR == (0xf6<<3|4)): assert(False);
            elif (self._IR == (0xf6<<3|5)): assert(False);
            elif (self._IR == (0xf6<<3|6)): assert(False);
            elif (self._IR == (0xf6<<3|7)): assert(False);

            # SMB7-zp
            elif (self._IR == (0xf7<<3|0)): assert(False);
            elif (self._IR == (0xf7<<3|1)): assert(False);
            elif (self._IR == (0xf7<<3|2)): assert(False);
            elif (self._IR == (0xf7<<3|3)): assert(False);
            elif (self._IR == (0xf7<<3|4)): assert(False);
            elif (self._IR == (0xf7<<3|5)): assert(False);
            elif (self._IR == (0xf7<<3|6)): assert(False);
            elif (self._IR == (0xf7<<3|7)): assert(False);

            # SED-i
            elif (self._IR == (0xf8<<3|0)): assert(False);
            elif (self._IR == (0xf8<<3|1)): assert(False);
            elif (self._IR == (0xf8<<3|2)): assert(False);
            elif (self._IR == (0xf8<<3|3)): assert(False);
            elif (self._IR == (0xf8<<3|4)): assert(False);
            elif (self._IR == (0xf8<<3|5)): assert(False);
            elif (self._IR == (0xf8<<3|6)): assert(False);
            elif (self._IR == (0xf8<<3|7)): assert(False);

            # SBC-a,y
            elif (self._IR == (0xf9<<3|0)): assert(False);
            elif (self._IR == (0xf9<<3|1)): assert(False);
            elif (self._IR == (0xf9<<3|2)): assert(False);
            elif (self._IR == (0xf9<<3|3)): assert(False);
            elif (self._IR == (0xf9<<3|4)): assert(False);
            elif (self._IR == (0xf9<<3|5)): assert(False);
            elif (self._IR == (0xf9<<3|6)): assert(False);
            elif (self._IR == (0xf9<<3|7)): assert(False);

            # PLX-s
            elif (self._IR == (0xfa<<3|0)): assert(False);
            elif (self._IR == (0xfa<<3|1)): assert(False);
            elif (self._IR == (0xfa<<3|2)): assert(False);
            elif (self._IR == (0xfa<<3|3)): assert(False);
            elif (self._IR == (0xfa<<3|4)): assert(False);
            elif (self._IR == (0xfa<<3|5)): assert(False);
            elif (self._IR == (0xfa<<3|6)): assert(False);
            elif (self._IR == (0xfa<<3|7)): assert(False);

            # None
            elif (self._IR == (0xfb<<3|0)): assert(False);
            elif (self._IR == (0xfb<<3|1)): assert(False);
            elif (self._IR == (0xfb<<3|2)): assert(False);
            elif (self._IR == (0xfb<<3|3)): assert(False);
            elif (self._IR == (0xfb<<3|4)): assert(False);
            elif (self._IR == (0xfb<<3|5)): assert(False);
            elif (self._IR == (0xfb<<3|6)): assert(False);
            elif (self._IR == (0xfb<<3|7)): assert(False);

            # None
            elif (self._IR == (0xfc<<3|0)): assert(False);
            elif (self._IR == (0xfc<<3|1)): assert(False);
            elif (self._IR == (0xfc<<3|2)): assert(False);
            elif (self._IR == (0xfc<<3|3)): assert(False);
            elif (self._IR == (0xfc<<3|4)): assert(False);
            elif (self._IR == (0xfc<<3|5)): assert(False);
            elif (self._IR == (0xfc<<3|6)): assert(False);
            elif (self._IR == (0xfc<<3|7)): assert(False);

            # SBC-a,x
            elif (self._IR == (0xfd<<3|0)): assert(False);
            elif (self._IR == (0xfd<<3|1)): assert(False);
            elif (self._IR == (0xfd<<3|2)): assert(False);
            elif (self._IR == (0xfd<<3|3)): assert(False);
            elif (self._IR == (0xfd<<3|4)): assert(False);
            elif (self._IR == (0xfd<<3|5)): assert(False);
            elif (self._IR == (0xfd<<3|6)): assert(False);
            elif (self._IR == (0xfd<<3|7)): assert(False);

            # INC-a,x
            elif (self._IR == (0xfe<<3|0)): assert(False);
            elif (self._IR == (0xfe<<3|1)): assert(False);
            elif (self._IR == (0xfe<<3|2)): assert(False);
            elif (self._IR == (0xfe<<3|3)): assert(False);
            elif (self._IR == (0xfe<<3|4)): assert(False);
            elif (self._IR == (0xfe<<3|5)): assert(False);
            elif (self._IR == (0xfe<<3|6)): assert(False);
            elif (self._IR == (0xfe<<3|7)): assert(False);

            # BBS7-r
            elif (self._IR == (0xff<<3|0)): assert(False);
            elif (self._IR == (0xff<<3|1)): assert(False);
            elif (self._IR == (0xff<<3|2)): assert(False);
            elif (self._IR == (0xff<<3|3)): assert(False);
            elif (self._IR == (0xff<<3|4)): assert(False);
            elif (self._IR == (0xff<<3|5)): assert(False);
            elif (self._IR == (0xff<<3|6)): assert(False);
            elif (self._IR == (0xff<<3|7)): assert(False);

            self._IR += 1

        self._PINS = self.circuit.pins
        return self.circuit.pins

    def flip(self, stdscr, y, x):
        lines = [f" PC: {to_hex(self._PC, 4)}",
                 f"  A: {to_hex(self._A, 2)}",
                 f"  X: {to_hex(self._X, 2)}",
                 f"  Y: {to_hex(self._Y, 2)}",
                 f"  S: {to_hex(self._S, 2)}",
                 f"  P: {to_hex(self._P, 2)}",
                 "",
                 f" IR: {to_hex(self._IR>>3, 2)} {to_bin(self._IR&7, 3)}",
                 f"BRK: {to_bin(self._brk_flags, 3)}"
                ]
        stdscr.addstr(y, x, "65C02")
        for row, line in enumerate(lines):
            stdscr.addstr(y+1 + row, x, line)

        lines = [
                     "+-----------------+",
                    f"| 1:VPB    RESB:40|",
                    f"| 2:RDY   PHI2O:39|",
                    f"| 3:PHI1O   SOB:38|",
                    f"| 4:IRQB   PHI2:37|",
                    f"| 5:MLB      BE:36|",
                    f"| 6:NMIB     NC:35|",
                    f"| 7:SYNC    RWB:34|",
                    f"| 8:VCC      D0:33|",
                    f"| 9: A0      D1:32|",
                    f"|10: A1      D2:31|",
                    f"|11: A2      D3:30|",
                    f"|12: A3      D4:29|",
                    f"|13: A4      D5:28|",
                    f"|14: A5      D6:27|",
                    f"|15: A6      D7:26|",
                    f"|16: A7     A15:25|",
                    f"|17: A8     A14:24|",
                    f"|18: A9     A13:23|",
                    f"|19:A10     A12:22|",
                    f"|20:A11     GND:21|",
                     "+-----------------+"
                ]
        for row, line in enumerate(lines):
            stdscr.addstr(y+1 + row, x+15, line)


if __name__ == "__main__":
    pins = 0b0000000000000000000000000000000000000000
    pins |= (M65C02_VCC|M65C02_RDY|M65C02_IRQB|M65C02_NMIB|M65C02_BE|M65C02_RESB)
    cpu = M65C02(pins)

    addr = 0xaa5a
    print(f"pins before _SA({to_bin(addr, 16)}):", to_bin(pins, 40))
    pins = M65C02._SA(pins, addr)
    print(f"pins after  _SA({to_bin(addr, 16)}):", to_bin(pins, 40))
    print(f"addr from _GA():{to_bin(M65C02._GA(pins), 16)}")
    print()

    pins = M65C02._SA(pins, 0x0000)

    data = 0xff
    print(f"pins before _SD({to_bin(data, 8)}):", to_bin(pins, 40))
    pins = M65C02._SD(pins, data)
    print(f"pins after  _SD({to_bin(data, 8)}):", to_bin(pins, 40))
    print(f"addr from _GD():{to_bin(M65C02._GD(pins), 8)}")
    print()

    pins = 0
    pins = M65C02._ON(pins, M65C02_RESB)
    print(f"pins after  on RESB:", to_bin(pins, 40))
    pins = ((1<<40)-1)
    pins = M65C02._OFF(pins, M65C02_RESB)
    print(f"pins after off RESB:", to_bin(pins, 40))
    print()

    pins = 0
    pins = M65C02._RD(pins)
    print(f"pins after _RD:", to_bin(pins, 40))
    pins = ((1<<40)-1)
    pins = M65C02._WR(pins)
    print(f"pins after _WR:", to_bin(pins, 40))
    print()

    print("_NZ tests:")
    print(to_bin(cpu._P, 8))
    M65C02._NZ(cpu, 0x10)
    print(to_bin(cpu._P, 8))
    M65C02._NZ(cpu, 0x00)
    print(to_bin(cpu._P, 8))
    M65C02._NZ(cpu, 0x80)
    print(to_bin(cpu._P, 8))
    M65C02._NZ(cpu, 0x05)
    print(to_bin(cpu._P, 8))
    print()

    print("_FETCH tests:")
    pins = 0
    cpu._PC = 0xaf05
    pins = M65C02._FETCH(pins, cpu)
    print(f"pins:", to_bin(pins, 40))
    print()

    print("_SAD tests:")
    pins = 0
    pins = M65C02._SAD(pins, 0x8001, 0x81)
    print(f"pins:", to_bin(pins, 40))
    print()


