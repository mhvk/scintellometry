
# __      __  _____    _   _____ 
# \ \    / / | ___ \  | | |   __|
#  \ \  / /  | |  | | | | |  |_  
#   \ \/ /   | |  | | | | |   _] 
#    \  /    | |__| | | | |  |   
#     \/     |_____/  |_| |__|   

from __future__ import division
import os

import numpy as np
from scipy.fftpack import fftfreq, fftshift
from astropy.io.fits import Header
from astropy.time import Time
import astropy.units as u

from . import MultiFile, header_defaults
from .multifile import good_name


class VDIFData(MultiFile):

    telescope = 'vdif'

    def __init__(self, raw_files, comm=None):
        """Pulsar data stored in the VDIF format"""

        checkfile = open(raw_files[0], 'rb')
        header = read_vdif_header(checkfile)
        # for complex is bits/complex component
        bips = header['bits_per_sample']
        self.data_is_complex = header['iscomplex']
        if self.data_is_complex:
            if bips == 8:
                dtype = 'ci1,ci1'
            elif bips == 2:
                dtype = 'ci0.5,ci0.5'
            else:
                raise ValueError("Can only do 8bit and 2bit complex")
        else:
            if bips == 2:
                dtype = '4bit'
            else:
                raise ValueError("Only 2bit real supported thus far.")

        self.islegacy = header['islegacy']
        self.header_size = 16 if self.islegacy else 32
        # includes header_size
        framesize = header['frame_size']
        blocksize = framesize - self.header_size
        frames_per_sec = count_frames_per_sec(checkfile, framesize, \
                                              header['secs_from_ref'])
        # could also do one second worth of data
        # blocksize = (framesize - self.header_size) * frames_per_sec
        nchan = header['nchan']
        self.time0 = get_time(header)
        self.npol = 2
        self.station = header['station_id']
        self.n_threads, self.thread_ids = get_n_threads(checkfile, framesize)
        if self.islegacy:
           self.samplerate = (blocksize / nchan) * \
                             (8/bips) * frames_per_sec * u.Hz
           # FIX ME!
           # if blocksize=data_per_second need to adjust samplerate!
        else:
            if header['edv']:
                self.sample_unit = u.MHz if header['sample_unit'] else u.kHz
                self.samplerate = header['sample_rate'] * self.sample_unit
            else:
                self.samplerate = (blocksize / nchan) * \
                                  (8/bips) * frames_per_sec * u.Hz

        self.dtsample = (nchan * 2 / self.samplerate).to(u.s)
        # might have multiple threads with each one holding one channel
        if nchan > 1 and self.n_threads > 1:
            raise ValueError("Multi-channel, multi-threaded vdif not supported.")
        if nchan == 1 and self.n_threads > 1:
            nchan = self.n_threads
        # in that case will need to get the freq of the different threads.
        # also for single thread multi-channel need to get freq from somewhere
        # probably via config file? check out dada.py for freq stuff
        if comm.rank == 0:
            print("In VDIFData, calling super")
            print("Start time: ", self.time0.iso)
        self.files = raw_files
        checkfile.close()

        super(VDIFData, self).__init__(raw_files, blocksize, dtype, nchan,
                                       comm=comm)

    def open(self, files, file_number=0):
        if file_number == self.current_file_number:
            return
            
        if self.current_file_number is not None:
            self.fh_raw.close()
        self.fh_raw = open(files[file_number], mode='rb')
        self.fh_raw.seek(self.header_size)
        self.current_file_number = file_number

    def close(self):
        """Close the whole file reader, unlinking links if needed."""
        if self.current_file_number is not None:
            self.fh_raw.close()

    def read(self, size):
        """Read size bytes, returning an ndarray with np.int8 dtype.

        Incorporate information from multiple underlying files if necessary.
        The current file pointer are assumed to be pointing at the right
        locations, i.e., just before the first bit of data that will be read.
        """
        if size % self.recordsize != 0:
            raise ValueError("Cannot read a non-integer number of records")

        # ensure we do not read beyond end
        size = min(size, len(self.files) * self.filesize - self.offset)
        if size <= 0:
            raise EOFError('At end of file in DADA.read')

        # allocate buffer for MPI read
        z = np.empty(size, dtype=np.int8)

        # read one or more pieces
        iz = 0
        while(iz < size):
            block, already_read = divmod(self.offset, self.filesize)
            fh_size = min(size - iz, self.filesize - already_read)
            z[iz:iz+fh_size] = np.fromstring(self.fh_raw.read(fh_size),
                                             dtype=z.dtype)
            self._seek(self.offset + fh_size)
            iz += fh_size

        return z

    def _seek(self, offset):
        assert offset % self.recordsize == 0
        file_number = offset // self.filesize
        file_offset = offset % self.filesize
        self.open(self.files, file_number)
        self.fh_raw.seek(file_offset + self.header_size)
        self.offset = offset

    def ntint(self, nchan):
        assert self.blocksize % (self.itemsize * nchan) == 0
        return self.blocksize // (self.itemsize * nchan)

    def __str__(self):
        return ('<DADAData nchan={0} dtype={1} blocksize={2}\n'
                'current_file_number={3}/{4} current_file={5}>'
                .format(self.nchan, self.dtype, self.blocksize,
                        self.current_file_number, len(self.files),
                        self.files[self.current_file_number]))

# DADA defaults for psrfits HDUs
# Note: these are largely made-up at this point
header_defaults['dada'] = {
    'PRIMARY': {'TELESCOP':'DADA',
                'IBEAM':1, 'FD_POLN':'LIN',
                'OBS_MODE':'SEARCH',
                'ANT_X':0, 'ANT_Y':0, 'ANT_Z':0, 'NRCVR':1,
                'FD_HAND':1, 'FD_SANG':0, 'FD_XYPH':0,
                'BE_PHASE':0, 'BE_DCC':0, 'BE_DELAY':0,
                'TRK_MODE':'TRACK',
                'TCYCLE':0, 'OBSFREQ':300, 'OBSBW':100,
                'OBSNCHAN':0, 'CHAN_DM':0,
                'EQUINOX':2000.0, 'BMAJ':1, 'BMIN':1, 'BPA':0,
                'SCANLEN':1, 'FA_REQ':0,
                'CAL_FREQ':0, 'CAL_DCYC':0, 'CAL_PHS':0, 'CAL_NPHS':0,
                'STT_IMJD':54000, 'STT_SMJD':0, 'STT_OFFS':0},
    'SUBINT': {'INT_TYPE': 'TIME',
               'SCALE': 'FluxDen',
               'POL_TYPE': 'AABB',
               'NPOL':1,
               'NBIN':1, 'NBIN_PRD':1,
               'PHS_OFFS':0,
               'NBITS':1,
               'ZERO_OFF':0, 'SIGNINT':0,
               'NSUBOFFS':0,
               'NCHAN':1,
               'CHAN_BW':1,
               'DM':0, 'RM':0, 'NCHNOFFS':0,
               'NSBLK':1}}


def read_vdif_header(raw_file):
    """
    read_vdif_header(raw_file):

    Reads 32/16byte-long (non-)legacy header in the first raw file.

    returns dictionary with all header entries.

    """
    bytestream = raw_file.read(32)

    header = np.fromstring(bytestream, dtype=np.uint32)
    # convert the 32bit word to binary, make sure to keep all zeros (need all 32 bits)
    header = np.array([bin(word)[2:].zfill(32) for word in header])

    # the first four words are always the same:
    standard = "".join(header[:4])
    lengths = [1,1,30,2,6,24,3,5,24,1,5,10]
    keys = ['isinvalid', 'islegacy', 'secs_from_ref', 'unassigned', 'ref_epoch',\
            'frame_nr', 'vdif_vers', 'nchans', 'frame_size', 'iscomplex',\
            'bits_per_sample', 'thread_id']
    d={}
    start = 0
    for i, key in enumerate(keys):
        length = lengths[i]
        d[key] = int(standard[start:start+length],2)
        start += length
    d['station_id'] = chr(int(standard[start:start+8],2)) + \
                      chr(int(standard[start+8:],2))
    d['bits_per_sample'] += 1
    d['frame_size'] *= 8
    d['nchans'] = 2**d['nchans']

    if d['islegacy']:
        return d

    # now get what's in the extension
    extension = "".join(header[4:])
    edv = int(extension[:8],2)

    if edv == 0:
        d['edv'] = 0

    elif edv == 1:
        # NICT version
        lengths = [8,1,23,32,32,32]
        keys = ['edv','sampling_unit', 'sample_rate', 'sync_pattern', 'das_id','ua']
        start = 0
        for i, key in enumerate(keys):
            length = lengths[i]
            d[key] = int(extension[start:start+length],2)
            start += length

    elif edv == 3:
        # VLBA version
        lengths = [8,1,23,32,32,4,4,4,3,1,4,4,8]
        keys = ['edv','sampling_unit', 'sample_rate', 'sync_pattern', 'loif_tuning',
                'unassigned2', 'dbe_unit', 'if_nr', 'subband', 'esb', 'major_rev',
                'minor_rev', 'personality']
        start = 0
        for i, key in enumerate(keys):
            length = lengths[i]
            d[key] = int(extension[start:start+length],2)
            start += length
        
    elif edv == 4:
        # MWA version
        lengths = [8,1,23,32]
        keys = ['edv','sampling_unit', 'sample_rate', 'sync_pattern']
        start = 0
        for i, key in enumerate(keys):
            length = lengths[i]
            d[key] = int(extension[start:start+length],2)
            start += length
    else:
        raise ValueError("Only VDIF extensions 0,1,3,4 supported at this stage.")

    
    return d

def get_time(dictionary):
    """
    get_time(dictionary)

    convert ref_epoch and secs_from_ref in header to acutal time xxxxYRxxxDxxHxxMxxSxxxMS

    retruns time-object.

    """
    if not 'ref_epoch' or not 'secs_from_ref' in dictionary.keys():
        raise TypeError("Need ref_epoch and secs_from_ref as keys in dictionary.")
    ref_epoch = dictionary['ref_epoch']
    secs_from_ref = dictionary['secs_from_ref']
    ref_year = 2000 + ref_epoch/2
    ref_month = 1 if ref_epoch % 2 == 0 else 7
    ref_mjd = Time(datetime(ref_year,ref_month,1,0,0,0),
                   format='datetime',scale='utc').mjd
    n_days = secs_from_ref / 86400.
    out = Time(ref_mjd + n_days, format='mjd', scale='utc')
    return out

def get_n_threads(infile, framesize):
    """
    get_n_threads(infile, framesize)

    infile can be a file object or a path to a file.

    returns the number of threads and their ID's in a vdif file.
    """
    f = infile
    # go to end of infile to get its size on disk
    f.seek(0,2)
    filesize = f.tell()
    assert filesize % framesize == 0
    n_total = filesize / framesize

    thread_ids = []
    for n in range(n_total):
        f.seek(n * framesize + 12)
        word3 = np.fromfile(f, count = 1, dtype=np.uint32)[0]
        thread_id = get_bit_vals(word3, 16, 10, np.uint32)
        if not thread_id in thread_ids:
            thread_ids.append(thread_id)

    return len(thread_ids), thread_ids

def count_frames_per_sec(infile, framesize, secs, thread_id=None):
    """
    count_frames_per_sec(infile, framesize, secs, thread_id=None)
    
    Returns the number of frames for that second in thread thread_id. 
    Per default counts # of frames for thread_id of very first header.

    infile can be either an opened file object or a path to a file.

    returns n_frames
    """

    f = infile
    # go to end of infile to get its size on disk
    f.seek(0,2)
    filesize = f.tell()
    assert filesize % framesize == 0
    n_total = filesize / framesize
    # set the thread to count frames for
    if not thread_id == None:
        thread_id_0 = thread_id
    else:
        f.seek(12)
        word3 = np.fromfile(f, count = 1, dtype=np.uint32)[0]
        thread_id_0 = get_bit_vals(word3, 16, 10, np.uint32)

    k=0
    for n in range(n_total):
        f.seek(n * framesize + 12)
        word3 = np.fromfile(f, count = 1, dtype=np.uint32)[0]
        thread_id = get_bit_vals(word3, 16, 10, np.uint32)
        if not thread_id == thread_id_0:
            continue
        f.seek(n * framesize)
        word0 = np.fromfile(f, count = 1, dtype=np.uint32)[0]
        sec = get_bit_vals(word0, 0, 30, np.uint32)
        if sec == secs:
            k += 1
    return k