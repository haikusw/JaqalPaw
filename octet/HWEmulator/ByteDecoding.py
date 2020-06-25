from octet.jaqalCompiler import mapFromBytes
# from .Utils.SystemParameters import *
# from .Utils.HelperFunctions import *
from octet.encodingParameters import *
from octet.pulseBinarization import pulse, convertFreqFull, convertAmpFull, convertPhaseFull
from collections import defaultdict
from octet.HWEmulator.pdqSpline import pdq_spline
import numpy as np
from octet.HWEmulator.URAM import URAM, URAMException, GLUT, PLUT, SLUT, GADDRW, PADDRW, SADDRW

tree = lambda: defaultdict(tree)

def convertPhaseBytesToReal(data):
    return data/(2**40-1)*360.0

def convertFreqBytesToReal(data):
    return data/(2**40-1)*CLOCK_FREQUENCY/1e6

def convertAmpBytesToReal(data):
    return (int(data)>>23)/(2**16-1)*200.0

def convertTimeFromClockCycles(data):
    return data*CLKPERIOD

mode_enum = { 'run': 0, 'bypass': 1, 'prog_plut': 2, 'prog_slut': 3, 'prog_glut': 4, None: None}
mode_lut = {v:k for k,v in mode_enum.items()}
mod_type_dict = {0b000: {'name': 'f0', 'machineConvFunc': convertFreqFull,  'realConvFunc': convertFreqBytesToReal},
                 0b001: {'name': 'a0', 'machineConvFunc': convertAmpFull,   'realConvFunc': convertAmpBytesToReal},
                 0b010: {'name': 'p0', 'machineConvFunc': convertPhaseFull, 'realConvFunc': convertPhaseBytesToReal},
                 0b011: {'name': 'f1', 'machineConvFunc': convertFreqFull,  'realConvFunc': convertFreqBytesToReal},
                 0b100: {'name': 'a1', 'machineConvFunc': convertAmpFull,   'realConvFunc': convertAmpBytesToReal},
                 0b101: {'name': 'p1', 'machineConvFunc': convertPhaseFull, 'realConvFunc': convertPhaseBytesToReal},
                 0b110: {'name': 'z0', 'machineConvFunc': convertPhaseFull, 'realConvFunc': convertPhaseBytesToReal},
                 0b111: {'name': 'z1', 'machineConvFunc': convertPhaseFull, 'realConvFunc': convertPhaseBytesToReal},
                 }

def parseGLUTProgData(data):
    """Program GLUT with input data word"""
    nwords = (data>>GLUT_BYTECNT_OFFSET)&0b11111
    channel = (data >> (DMA_MUX_OFFSET)) & 0b111
    for w in range(nwords):
        sdata = data>>(w*(2*SADDRW+GADDRW))
        glut_data = sdata&(2**(2*SADDRW)-1)
        glut_addr = (sdata>>(2*SADDRW))&(2**GADDRW-1)
        GLUT[channel][glut_addr] = glut_data

def parseSLUTProgData(data):
    """Program SLUT with input data word"""
    nwords = (data>>SLUT_BYTECNT_OFFSET)&0b11111
    channel = (data >> DMA_MUX_OFFSET) & 0b111
    for w in range(nwords):
        sdata = data>>(w*(PADDRW+SADDRW))
        slut_data = sdata&(2**PADDRW-1)
        slut_addr = (sdata>>PADDRW)&(2**SADDRW-1)
        SLUT[channel][slut_addr] = slut_data

def parsePLUTProgData(data):
    """Program PLUT with input data word"""
    newdata = int.from_bytes(data, byteorder='little', signed=False)
    plut_addr = (newdata >> PLUT_BYTECNT_OFFSET) & (2**PADDRW-1)
    channel = (newdata >> DMA_MUX_OFFSET) & 0b111
    PLUT[channel][plut_addr] = data

def iterateGLUTBounds(gid, channel):
    """Get all PLUT data for an individual gate"""
    bounds_bytes = GLUT[channel][gid]
    start = bounds_bytes & (2**SADDRW-1)
    stop = (bounds_bytes>>SADDRW) & (2**SADDRW-1)
    for sid in range(start,stop+1):
        yield PLUT[channel][SLUT[channel][sid]]

def parseGSeqData(data):
    """Get sequence of gates to run from input data"""
    prog_byte_cnt = (data >> GSEQ_BYTECNT_OFFSET) & 0b111111
    channel = (data >> DMA_MUX_OFFSET) & 0b111
    newdata = data
    plut_list = []
    gidlist = []
    for g in range(prog_byte_cnt):
        gid = newdata & (2**GADDRW-1)
        newdata >>= GADDRW
        gidlist.append(gid)
        for plut_data in iterateGLUTBounds(gid, channel):
            plut_list.append(plut_data)
    print(f"gid list {channel}: {gidlist}")
    return plut_list

def parseBypassData(data):
    """Return parameters for a raw data word"""
    U0, U1, U2, U3, dur = mapFromBytes(data)
    return dur, U0, U1, U2, U3

def DecodeWord(raw_data, master_data_record, sequence_mode=False):
    """This function essentially acts like the data path from DMA to the spline engine output.
       Input words are 256 bits, and are parsed and treated accordingly depending on the
       metadata tags in the raw data in order to program LUTs or run gate sequences etc...
       The output is stored in a recursive default dict which is passed in to master_data_record"""
    data = int.from_bytes(raw_data, byteorder='little', signed=False)
    mod_type = (data >> MODTYPE_LSB) & 0b111
    shift = (data >> SPLSHIFT_LSB) & 0b11111
    prog_mode = (data >> PROG_MODE_OFFSET) & 0b111
    prog_byte_cnt = None
    channel = (data >> DMA_MUX_OFFSET) & 0b111
    dur, U0, U1, U2, U3 = None, None, None, None, None
    waittrig = (data >> WAIT_TRIG_LSB) & 0b1
    enablemask = (data >> OUTPUT_EN_LSB) & 0b11
    mode =None
    if prog_mode == 0b111 or sequence_mode:
        mode = mode_enum['bypass']
        dur, U0, U1, U2, U3 = parseBypassData(raw_data)
    elif prog_mode == 0b001:
        prog_byte_cnt = (data >> GLUT_BYTECNT_OFFSET) & 0b11111111
        mode = mode_enum['prog_glut']
        parseGLUTProgData(data)
    elif prog_mode == 0b010:
        prog_byte_cnt = (data >> SLUT_BYTECNT_OFFSET) & 0b11111111
        mode = mode_enum['prog_slut']
        parseSLUTProgData(data)
    elif prog_mode == 0b011:
        prog_byte_cnt = (data >> PLUT_BYTECNT_OFFSET) & 0b11111111
        mode = mode_enum['prog_plut']
        parsePLUTProgData(raw_data)
    elif prog_mode == 0b100 or prog_mode == 0b101 or prog_mode == 0b110:
        prog_byte_cnt = (data >> GSEQ_BYTECNT_OFFSET) & 0b11111111
        mode = mode_enum['run']
        for gs_data in parseGSeqData(data):
            master_data_record = DecodeWord(gs_data, master_data_record, sequence_mode=True)

    print(f"channel: {channel}, mod type: {mod_type_dict[mod_type]['name']}, mode: {mode_lut[mode]}, shift: {shift}, prog byte count: {prog_byte_cnt}")

    if mode == mode_enum['bypass']:
        dur_real = convertTimeFromClockCycles(dur)
        U0_real = mod_type_dict[mod_type]['realConvFunc'](U0)
        U1_real = mod_type_dict[mod_type]['realConvFunc'](U1)
        U2_real = mod_type_dict[mod_type]['realConvFunc'](U2)
        U3_real = mod_type_dict[mod_type]['realConvFunc'](U3)
        print(f"Duration: {dur_real} s, U0: {U0_real}, U1: {U1_real}, U2: {U2_real}, U3: {U3_real}")
        if isinstance(master_data_record[channel][mod_type]['time'], defaultdict):
            master_data_record[channel][mod_type]['time'] = [0]
        if isinstance(master_data_record[channel][mod_type]['data'], defaultdict):
            master_data_record[channel][mod_type]['data'] = [0]
        if isinstance(master_data_record[channel][mod_type]['waittrig'], defaultdict):
            master_data_record[channel][mod_type]['waittrig'] = [waittrig]
        if isinstance(master_data_record[channel][mod_type]['enablemask'], defaultdict):
            master_data_record[channel][mod_type]['enablemask'] = [enablemask]
        if U1 == 0 and U2 == 0 and U3 == 0 and False:
            master_data_record[channel][mod_type]['time'].append(master_data_record[channel][mod_type]['time'][-1]+dur_real)
            master_data_record[channel][mod_type]['data'].append(U0_real)
            master_data_record[channel][mod_type]['waittrig'].append(waittrig)
            master_data_record[channel][mod_type]['enablemask'].append(enablemask)
        else:
            U1_shift = U1/(2**(shift*1))
            U2_shift = U2/(2**(shift*2))
            U3_shift = U3/(2**(shift*3))
            U1_rshift = U1_real/(2**shift)
            U2_rshift = U2_real/(2**(shift*2))
            U3_rshift = U3_real/(2**(shift*3))
            coeffs = np.zeros((4,1))
            coeffs[0,0] = U3_shift
            coeffs[1,0] = U2_shift
            coeffs[2,0] = U1_shift
            coeffs[3,0] = U0
            xdata = np.array(list(range(dur)))+1
            spline_data = pdq_spline(coeffs, [0], nsteps=dur)
            spline_data_real = list(map(mod_type_dict[mod_type]['realConvFunc'], spline_data))
            xdata_real = list(map(lambda x: master_data_record[channel][mod_type]['time'][-1]+convertTimeFromClockCycles(x), xdata))
            master_data_record[channel][mod_type]['time'].extend(xdata_real)
            del master_data_record[channel][mod_type]['data'][-1]
            master_data_record[channel][mod_type]['data'].extend(spline_data_real)
            master_data_record[channel][mod_type]['data'].append(spline_data_real[-1])

            master_data_record[channel][mod_type]['waittrig'].append([waittrig]+[0]*(len(xdata_real)-1))
            master_data_record[channel][mod_type]['enablemask'].append([enablemask]*len(xdata_real))
            print(f"Duration: {dur_real} s, U0: {U0}, U1: {U1_rshift}, U2: {U2_rshift}, U3: {U3_rshift}")


    return master_data_record


mdr = tree()
if __name__ == '__main__':
    import matplotlib.pyplot as plt

    pulse_data = pulse(3, 9.23e-6,
                       freq0=[42.43, 123, 32, 32],
                       amp0=(0, 10, 30, 50, 30, 10, 0),
                       phase0=81,
                       freq1=(0, 140, 190, 120, 155, 75, 0),
                       phase1=[122, 70, 10, 2],
                       amp1=45,
                       framerot0=(20, 56, 280),
                       framerot1=(1, 10, 25, 80),
                       bypass=True)

    for pd in pulse_data:
        DecodeWord(pd, master_data_record=mdr)

    print(mdr)
    f, axl = plt.subplots(4, 2, sharex=True)
    for i in range(4):
        for j in range(2):
            axl[i][j].set_ylabel(mod_type_dict[i + j * 4]['name'])
            axl[i][j].step(mdr[3][i + j * 4]['time'], mdr[3][i + j * 4]['data'], where='post')
    plt.show(block=True)

print(GLUT)
print(SLUT)
print(PLUT)
