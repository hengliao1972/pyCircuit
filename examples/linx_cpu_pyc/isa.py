from __future__ import annotations

# NOTE: These op IDs are an internal encoding for the LinxISA bring-up CPU model.
# Keep them stable across backends/tests (C++ + Verilog).

OP_INVALID = 0
OP_C_BSTART_STD = 1
OP_C_BSTOP = 2
OP_C_MOVR = 3
OP_C_LWI = 4
OP_C_SWI = 5
OP_SUBI = 6
OP_ADDI = 7
OP_ADDIW = 8
OP_LWI = 9
OP_SWI = 10
OP_ADDW = 11
OP_ORW = 12
OP_ANDW = 13
OP_XORW = 14
OP_CMP_EQ = 15
OP_CSEL = 16
OP_HL_LUI = 17
OP_EBREAK = 18

# --- extended ops for BlockISA control flow + PC-relative + wider memory ---
OP_C_BSTART_COND = 19
OP_BSTART_STD_CALL = 20
OP_C_MOVI = 21
OP_C_SETRET = 22
OP_C_SETC_EQ = 23
OP_C_SETC_TGT = 24
OP_ADDTPC = 25
OP_SDI = 26

# --- qemu-tests bring-up ops (integer) ---
OP_LUI = 27
OP_SETRET = 28
OP_ADD = 29
OP_SUB = 30
OP_OR = 31
OP_ANDI = 32
OP_ANDIW = 33
OP_ORI = 34
OP_ORIW = 35
OP_SLL = 36
OP_SLLI = 37
OP_SRL = 38
OP_SRLIW = 39
OP_BXU = 40
OP_C_ADD = 41
OP_C_ADDI = 42
OP_C_OR = 43
OP_C_LDI = 44
OP_C_SDI = 45
OP_C_SEXT_W = 46
OP_C_ZEXT_W = 47
OP_C_SETC_NE = 48
OP_CMP_LTU = 49
OP_CMP_LTUI = 50
OP_SETC_GEUI = 51
OP_LBUI = 52
OP_LB = 53
OP_LW = 54
OP_LDI = 55
OP_HL_LW_PCR = 56
OP_HL_SW_PCR = 57

# --- macro / multi-cycle ops ---
OP_FENTRY = 58
OP_FEXIT = 59
OP_FRET_RA = 60
OP_FRET_STK = 61

# --- additional BlockISA markers ---
OP_C_BSTART_DIRECT = 62
OP_BSTART_STD_FALL = 63
OP_BSTART_STD_DIRECT = 64
OP_BSTART_STD_COND = 65

# --- extended HL.*.PCR ops (funct3 encodes width/signedness) ---
OP_HL_LB_PCR = 66
OP_HL_LBU_PCR = 67
OP_HL_LH_PCR = 68
OP_HL_LHU_PCR = 69
OP_HL_LWU_PCR = 70
OP_HL_LD_PCR = 71
OP_HL_SB_PCR = 72
OP_HL_SH_PCR = 73
OP_HL_SD_PCR = 74

# --- additional memory ops (benchmarks / full ISA bring-up) ---
# Immediate offset loads.
OP_LBI = 75
OP_LHI = 76
OP_LHUI = 77
OP_LWUI = 78

# Indexed loads.
OP_LBU = 79
OP_LH = 80
OP_LHU = 81
OP_LWU = 82
OP_LD = 83

# Immediate offset stores.
OP_SBI = 84
OP_SHI = 85

# Indexed stores.
OP_SB = 86
OP_SH = 87
OP_SW = 88
OP_SD = 89

# --- SETC.* (BlockISA commit condition setters) ---
OP_SETC_EQ = 90
OP_SETC_NE = 91
OP_SETC_AND = 92
OP_SETC_OR = 93
OP_SETC_LT = 94
OP_SETC_LTU = 95
OP_SETC_GE = 96
OP_SETC_GEU = 97

# SETC.* immediate variants (opcode 0x75 in the ISA; shamt comes from bits[11:7]).
OP_SETC_EQI = 98
OP_SETC_NEI = 99
OP_SETC_ANDI = 100
OP_SETC_ORI = 101
OP_SETC_LTI = 102
OP_SETC_GEI = 103
OP_SETC_LTUI = 104

# --- CMP.* immediate variants (write 0/1 to RegDst) ---
OP_CMP_EQI = 105
OP_CMP_NEI = 106
OP_CMP_ANDI = 107
OP_CMP_ORI = 108
OP_CMP_LTI = 109
OP_CMP_GEI = 110
OP_CMP_GEUI = 111

# --- Mul/Div/Rem (benchmarks) ---
OP_MUL = 112
OP_MULW = 113
OP_DIV = 114
OP_DIVU = 115
OP_DIVW = 116
OP_DIVUW = 117
OP_REM = 118
OP_REMU = 119
OP_REMW = 120
OP_REMUW = 121

# Optional 4-operand fused ops (decoder uses SrcD in bits[31:27], mapped to `srcp`).
OP_MADD = 122
OP_MADDW = 123

# --- additional bring-up ops (benchmarks / ISA completeness) ---
OP_AND = 124
OP_BXS = 125
OP_SLLIW = 126
OP_SUBW = 127
OP_SRAIW = 128
OP_CMP_NE = 129
OP_C_SUB = 130
OP_C_AND = 131
OP_SUBIW = 132
OP_SRA = 133
OP_XOR = 134
OP_XORIW = 135
OP_CMP_LT = 136

# --- template blocks (standalone; restartable) ---
OP_MCOPY = 137
OP_MSET = 138

# --- decoupled blocks (header + out-of-line body) ---
OP_BSTART_TMA = 139
OP_BSTART_CUBE = 140
OP_BSTART_VPAR = 141
OP_BSTART_VSEQ = 142

# --- block header descriptors ---
OP_B_TEXT = 143
OP_B_IOT = 144
OP_B_IOTI = 145
OP_B_IOR = 146
OP_B_ATTR = 147
OP_B_DIM = 148

REG_INVALID = 0x3F

ST_IF = 0
ST_ID = 1
ST_EX = 2
ST_MEM = 3
ST_WB = 4

# Block transition kinds (internal control state, not ISA encodings).
BK_FALL = 0
BK_COND = 1
BK_CALL = 2
BK_RET = 3
BK_DIRECT = 4
BK_IND = 5
BK_ICALL = 6
