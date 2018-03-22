# -*- Mode: Python; test-case-name: whipper.test.test_image_table -*-
# vi:si:et:sw=4:sts=4:ts=4

# Copyright (C) 2009 Thomas Vander Stichele

# This file is part of whipper.
#
# whipper is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# whipper is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with whipper.  If not, see <http://www.gnu.org/licenses/>.

"""
Wrap Table of Contents.
"""

import copy
import urllib
import urlparse

import whipper

from whipper.common import common, config

import logging
logger = logging.getLogger(__name__)

# FIXME: taken from libcdio, but no reference found for these

CDTEXT_FIELDS = [
    'ARRANGER',
    'COMPOSER',
    'DISCID',
    'GENRE',
    'MESSAGE',
    'ISRC',
    'PERFORMER',
    'SIZE_INFO',
    'SONGWRITER',
    'TITLE',
    'TOC_INFO',
    'TOC_INFO2',
    'UPC_EAN',
]


class Track:
    """
    I represent a track entry in an Table.

    @ivar number:       track number (1-based)
    @type number:       int
    @ivar audio:        whether the track is audio
    @type audio:        bool
    @type indexes:      dict of number -> L{Index}
    @ivar isrc:         ISRC code (12 alphanumeric characters)
    @type isrc:         str
    @ivar cdtext:       dictionary of CD Text information; see L{CDTEXT_KEYS}.
    @type cdtext:       str -> unicode
    @ivar pre_emphasis: whether track is pre-emphasised
    @type pre_emphasis: bool
    """

    number = None
    audio = None
    indexes = None
    isrc = None
    cdtext = None
    session = None
    pre_emphasis = None

    def __repr__(self):
        return '<Track %02d>' % self.number

    def __init__(self, number, audio=True, session=None):
        self.number = number
        self.audio = audio
        self.indexes = {}
        self.cdtext = {}

    def index(self, number, absolute=None, path=None, relative=None,
              counter=None):
        """
        @type path:  unicode or None
        """
        if path is not None:
            assert type(path) is unicode, "%r is not unicode" % path

        i = Index(number, absolute, path, relative, counter)
        self.indexes[number] = i

    def getIndex(self, number):
        return self.indexes[number]

    def getFirstIndex(self):
        """
        Get the first chronological index for this track.

        Typically this is INDEX 01; but it could be INDEX 00 if there's
        a pre-gap.
        """
        indexes = self.indexes.keys()
        indexes.sort()
        return self.indexes[indexes[0]]

    def getLastIndex(self):
        indexes = self.indexes.keys()
        indexes.sort()
        return self.indexes[indexes[-1]]

    def getPregap(self):
        """
        Returns the length of the pregap for this track.

        The pregap is 0 if there is no index 0, and the difference between
        index 1 and index 0 if there is.
        """
        if 0 not in self.indexes:
            return 0

        return self.indexes[1].absolute - self.indexes[0].absolute


class Index:
    """
    @ivar counter: counter for the index source; distinguishes between
                   the matching FILE lines in .cue files for example
    @type path:    unicode or None
    """
    number = None
    absolute = None
    path = None
    relative = None
    counter = None

    def __init__(self, number, absolute=None, path=None, relative=None,
                 counter=None):

        if path is not None:
            assert type(path) is unicode, "%r is not unicode" % path

        self.number = number
        self.absolute = absolute
        self.path = path
        self.relative = relative
        self.counter = counter

    def __repr__(self):
        return '<Index %02d absolute %r path %r relative %r counter %r>' % (
            self.number, self.absolute, self.path, self.relative, self.counter)


class Table(object):
    """
    I represent a table of indexes on a CD.

    @ivar tracks:  tracks on this CD
    @type tracks:  list of L{Track}
    @ivar catalog: catalog number
    @type catalog: str
    @type cdtext:  dict of str -> str
    """

    tracks = None  # list of Track
    leadout = None  # offset where the leadout starts
    catalog = None  # catalog number; FIXME: is this UPC ?
    cdtext = None
    mbdiscid = None

    classVersion = 4

    def __init__(self, tracks=None):
        if not tracks:
            tracks = []

        self.tracks = tracks
        self.cdtext = {}
        # done this way because just having a class-defined instance var
        # gets overridden when unpickling
        self.instanceVersion = self.classVersion
        self.unpickled()

    def unpickled(self):
        self.logName = "Table 0x%08x v%d" % (id(self), self.instanceVersion)
        logger.debug('set logName')

    def getTrackStart(self, number):
        """
        @param number: the track number, 1-based
        @type  number: int

        @returns: the start of the given track number's index 1, in CD frames
        @rtype:   int
        """
        track = self.tracks[number - 1]
        return track.getIndex(1).absolute

    def getTrackEnd(self, number):
        """
        @param number: the track number, 1-based
        @type  number: int

        @returns: the end of the given track number (ie index 1 of next track)
        @rtype:   int
        """
        # default to end of disc
        end = self.leadout - 1

        # if not last track, calculate it from the next track
        if number < len(self.tracks):
            end = self.tracks[number].getIndex(1).absolute - 1

            # if on a session border, subtract the session leadin
            thisTrack = self.tracks[number - 1]
            nextTrack = self.tracks[number]
            if nextTrack.session > thisTrack.session:
                gap = self._getSessionGap(nextTrack.session)
                end -= gap

        return end

    def getTrackLength(self, number):
        """
        @param number: the track number, 1-based
        @type  number: int

        @returns: the length of the given track number, in CD frames
        @rtype:   int
        """
        return self.getTrackEnd(number) - self.getTrackStart(number) + 1

    def getAudioTracks(self):
        """
        @returns: the number of audio tracks on the CD
        @rtype:   int
        """
        return len([t for t in self.tracks if t.audio])

    def hasDataTracks(self):
        """
        @returns: whether this disc contains data tracks
        """
        return len([t for t in self.tracks if not t.audio]) > 0

    def _cddbSum(self, i):
        ret = 0
        while i > 0:
            ret += (i % 10)
            i /= 10

        return ret

    def getCDDBValues(self):
        """
        Get all CDDB values needed to calculate disc id and lookup URL.

        This includes:
         - CDDB disc id
         - number of audio tracks
         - offset of index 1 of each track
         - length of disc in seconds (including data track)

        @rtype:   list of int
        """
        result = []

        result.append(self.getAudioTracks())

        # cddb disc id takes into account data tracks
        # last byte is the number of tracks on the CD
        n = 0

        # CD's have a standard lead-in time of 2 seconds
        # which gets added for CDDB disc id's
        delta = 2 * common.FRAMES_PER_SECOND

        debug = [str(len(self.tracks))]
        for track in self.tracks:
            offset = self.getTrackStart(track.number) + delta
            result.append(offset)
            debug.append(str(offset))
            seconds = offset / common.FRAMES_PER_SECOND
            n += self._cddbSum(seconds)

        # the 'real' leadout, not offset by 150 frames
        last = self.tracks[-1]
        leadout = self.getTrackEnd(last.number) + 1
        logger.debug('leadout LBA: %d', leadout)

        # FIXME: we can't replace these calculations with the getFrameLength
        # call because the start and leadout in the algorithm get rounded
        # before making the difference
        startSeconds = self.getTrackStart(1) / common.FRAMES_PER_SECOND
        leadoutSeconds = leadout / common.FRAMES_PER_SECOND
        t = leadoutSeconds - startSeconds
        # durationFrames = self.getFrameLength(data=True)
        # duration = durationFrames / common.FRAMES_PER_SECOND
        # assert t == duration, "%r != %r" % (t, duration)

        debug.append(str(leadoutSeconds + 2))  # 2 is the 150 frame cddb offset
        result.append(leadoutSeconds)

        value = (n % 0xff) << 24 | t << 8 | len(self.tracks)
        result.insert(0, value)

        # compare this debug line to cd-discid output
        logger.debug('cddb values: %r', result)

        logger.debug('cddb disc id debug: %s',
                     " ".join(["%08x" % value, ] + debug))

        return result

    def getCDDBDiscId(self):
        """
        Calculate the CDDB disc ID.

        @rtype:   str
        @returns: the 8-character hexadecimal disc ID
        """
        values = self.getCDDBValues()
        return "%08x" % values[0]

    def getMusicBrainzDiscId(self):
        """
        Calculate the MusicBrainz disc ID.

        @rtype:   str
        @returns: the 28-character base64-encoded disc ID
        """
        if self.mbdiscid:
            logger.debug('getMusicBrainzDiscId: returning cached %r'
                         % self.mbdiscid)
            return self.mbdiscid
        values = self._getMusicBrainzValues()

        # MusicBrainz disc id does not take into account data tracks
        # P2.3
        try:
            import hashlib
            sha1 = hashlib.sha1
        except ImportError:
            from sha import sha as sha1
        import base64

        sha = sha1()

        # number of first track
        sha.update("%02X" % values[0])

        # number of last track
        sha.update("%02X" % values[1])

        sha.update("%08X" % values[2])

        # offsets of tracks
        for i in range(1, 100):
            try:
                offset = values[2 + i]
            except IndexError:
                offset = 0
            sha.update("%08X" % offset)

        digest = sha.digest()
        assert len(digest) == 20, \
            "digest should be 20 chars, not %d" % len(digest)

        # The RFC822 spec uses +, /, and = characters, all of which are special
        # HTTP/URL characters. To avoid the problems with dealing with that, I
        # (Rob) used ., _, and -

        # base64 altchars specify replacements for + and /
        result = base64.b64encode(digest, '._')

        # now replace =
        result = "-".join(result.split("="))
        assert len(result) == 28, \
            "Result should be 28 characters, not %d" % len(result)

        logger.debug('getMusicBrainzDiscId: returning %r' % result)
        self.mbdiscid = result
        return result

    def getMusicBrainzSubmitURL(self):
        host = config.Config().get_musicbrainz_server()

        discid = self.getMusicBrainzDiscId()
        values = self._getMusicBrainzValues()

        query = urllib.urlencode({
            'id': discid,
            'toc': ' '.join([str(v) for v in values]),
            'tracks': self.getAudioTracks(),
        })

        return urlparse.urlunparse((
            'https', host, '/cdtoc/attach', '', query, ''))

    def getFrameLength(self, data=False):
        """
        Get the length in frames (excluding HTOA)

        @param data: whether to include the data tracks in the length
        """
        # the 'real' leadout, not offset by 150 frames
        if data:
            last = self.tracks[-1]
        else:
            last = self.tracks[self.getAudioTracks() - 1]

        leadout = self.getTrackEnd(last.number) + 1
        logger.debug('leadout LBA: %d', leadout)
        durationFrames = leadout - self.getTrackStart(1)

        return durationFrames

    def duration(self):
        """
        Get the duration in ms for all audio tracks (excluding HTOA).
        """
        return int(self.getFrameLength() * 1000.0 / common.FRAMES_PER_SECOND)

    def _getMusicBrainzValues(self):
        """
        Get all MusicBrainz values needed to calculate disc id and submit URL.

        This includes:
         - track number of first track
         - number of audio tracks
         - leadout of disc
         - offset of index 1 of each track

        @rtype:   list of int
        """
        # MusicBrainz disc id does not take into account data tracks

        result = []

        # number of first track
        result.append(1)

        # number of last audio track
        result.append(self.getAudioTracks())

        leadout = self.leadout
        # if the disc is multi-session, last track is the data track,
        # and we should subtract 11250 + 150 from the last track's offset
        # for the leadout
        if self.hasDataTracks():
            assert not self.tracks[-1].audio
            leadout = self.tracks[-1].getIndex(1).absolute - 11250 - 150

        # treat leadout offset as track 0 offset
        result.append(150 + leadout)

        # offsets of tracks
        for i in range(1, 100):
            try:
                track = self.tracks[i - 1]
                if not track.audio:
                    continue
                offset = track.getIndex(1).absolute + 150
                result.append(offset)
            except IndexError:
                pass

        logger.debug('MusicBrainz values: %r', result)
        return result

    def cue(self, cuePath='', program='whipper'):
        """
        @param cuePath: path to the cue file to be written. If empty,
                        will treat paths as if in current directory.


        Dump our internal representation to a .cue file content.

        @rtype: C{unicode}
        """
        logger.debug('generating .cue for cuePath %r', cuePath)

        lines = []

        def writeFile(path):
            targetPath = common.getRelativePath(path, cuePath)
            line = 'FILE "%s" WAVE' % targetPath
            lines.append(line)
            logger.debug('writeFile: %r' % line)

        # header
        main = ['PERFORMER', 'TITLE']

        for key in CDTEXT_FIELDS:
            if key not in main and key in self.cdtext:
                lines.append("    %s %s" % (key, self.cdtext[key]))

        assert self.hasTOC(), "Table does not represent a full CD TOC"
        lines.append('REM DISCID %s' % self.getCDDBDiscId().upper())
        lines.append('REM COMMENT "%s %s"' % (program, whipper.__version__))

        if self.catalog:
            lines.append("CATALOG %s" % self.catalog)

        for key in main:
            if key in self.cdtext:
                lines.append('%s "%s"' % (key, self.cdtext[key]))

        # FIXME:
        # - the first FILE statement goes before the first TRACK, even if
        #   there is a non-file-using PREGAP
        # - the following FILE statements come after the last INDEX that
        #   use that FILE; so before a next TRACK, PREGAP silence, ...

        # add the first FILE line; EAC always puts the first FILE
        # statement before TRACK 01 and any possible PRE-GAP
        firstTrack = self.tracks[0]
        index = firstTrack.getFirstIndex()
        indexOne = firstTrack.getIndex(1)
        counter = index.counter
        track = firstTrack

        while not index.path:
            t, i = self.getNextTrackIndex(track.number, index.number)
            track = self.tracks[t - 1]
            index = track.getIndex(i)
            counter = index.counter

        if index.path:
            logger.debug('counter %d, writeFile' % counter)
            writeFile(index.path)

        for i, track in enumerate(self.tracks):
            logger.debug('track i %r, track %r' % (i, track))
            # FIXME: skip data tracks for now
            if not track.audio:
                continue

            indexes = track.indexes.keys()
            indexes.sort()

            wroteTrack = False

            for number in indexes:
                index = track.indexes[number]
                logger.debug('index %r, %r' % (number, index))

                # any time the source counter changes to a higher value,
                # write a FILE statement
                # it has to be higher, because we can run into the HTOA
                # at counter 0 here
                if index.counter > counter:
                    if index.path:
                        logger.debug('counter %d, writeFile' % counter)
                        writeFile(index.path)
                    logger.debug('setting counter to index.counter %r' %
                                 index.counter)
                    counter = index.counter

                # any time we hit the first index, write a TRACK statement
                if not wroteTrack:
                    wroteTrack = True
                    line = "  TRACK %02d %s" % (i + 1, 'AUDIO')
                    lines.append(line)
                    logger.debug('%r' % line)

                    for key in CDTEXT_FIELDS:
                        if key in track.cdtext:
                            lines.append('    %s "%s"' % (
                                key, track.cdtext[key]))

                    if track.isrc is not None:
                        lines.append("    ISRC %s" % track.isrc)

                    if track.pre_emphasis is not None:
                        lines.append("    FLAGS PRE")

                    # handle TRACK 01 INDEX 00 specially
                    if 0 in indexes:
                        index00 = track.indexes[0]
                        if i == 0:
                            # if we have a silent pre-gap, output it
                            if not index00.path:
                                length = indexOne.absolute - index00.absolute
                                lines.append("    PREGAP %s" %
                                             common.framesToMSF(length))
                                continue

                        # handle any other INDEX 00 after its TRACK
                        lines.append("    INDEX "
                                     "%02d %s" % (0, common.framesToMSF(
                                                        index00.relative)))

                if number > 0:
                    # index 00 is output after TRACK up above
                    lines.append("    INDEX %02d %s" % (number,
                                                        common.framesToMSF(
                                                            index.relative)))

        lines.append("")

        return "\n".join(lines)

    # methods that modify the table

    def clearFiles(self):
        """
        Clear all file backings.
        Resets indexes paths and relative offsets.
        """
        # FIXME: do a loop over track indexes better, with a pythonic
        # construct that allows you to do for t, i in ...
        t = self.tracks[0].number
        index = self.tracks[0].getFirstIndex()
        i = index.number

        logger.debug('clearing path')
        while True:
            track = self.tracks[t - 1]
            index = track.getIndex(i)
            logger.debug('Clearing path on track %d, index %d', t, i)
            index.path = None
            index.relative = None
            try:
                t, i = self.getNextTrackIndex(t, i)
            except IndexError:
                break

    def setFile(self, track, index, path, length, counter=None):
        """
        Sets the given file as the source from the given index on.
        Will loop over all indexes that fall within the given length,
        to adjust the path.

        Assumes all indexes have an absolute offset and will raise if not.

        @type  track: C{int}
        @type  index: C{int}
        """
        logger.debug('setFile: track %d, index %d, path %r, '
                     'length %r, counter %r', track, index, path, length,
                     counter)

        t = self.tracks[track - 1]
        i = t.indexes[index]
        start = i.absolute
        assert start is not None, "index %r is missing absolute offset" % i
        end = start + length - 1  # last sector that should come from this file

        # FIXME: check border conditions here, esp. wrt. toc's off-by-one bug
        while i.absolute <= end:
            i.path = path
            i.relative = i.absolute - start
            i.counter = counter
            logger.debug('Setting path %r, relative %r on '
                         'track %d, index %d, counter %r',
                         path, i.relative, track, index, counter)
            try:
                track, index = self.getNextTrackIndex(track, index)
                t = self.tracks[track - 1]
                i = t.indexes[index]
            except IndexError:
                break

    def absolutize(self):
        """
        Calculate absolute offsets on indexes as much as possible.
        Only possible for as long as tracks draw from the same file.
        """
        t = self.tracks[0].number
        index = self.tracks[0].getFirstIndex()
        i = index.number
        # the first cut is the deepest
        counter = index.counter

        logger.debug('absolutizing')
        while True:
            track = self.tracks[t - 1]
            index = track.getIndex(i)
            assert track.number == t
            assert index.number == i
            if index.counter is None:
                logger.debug('Track %d, index %d has no counter', t, i)
                break
            if index.counter != counter:
                logger.debug(
                    'Track %d, index %d has a different counter', t, i)
                break
            logger.debug('Setting absolute offset %d on track %d, index %d',
                         index.relative, t, i)
            if index.absolute is not None:
                if index.absolute != index.relative:
                    msg = 'Track %d, index %d had absolute %d,' \
                        ' overriding with %d' % (
                            t, i, index.absolute, index.relative)
                    raise ValueError(msg)
            index.absolute = index.relative
            try:
                t, i = self.getNextTrackIndex(t, i)
            except IndexError:
                break

    def merge(self, other, session=2):
        """
        Merges the given table at the end.
        The other table is assumed to be from an additional session,


        @type  other: L{Table}
        """
        gap = self._getSessionGap(session)

        trackCount = len(self.tracks)
        sourceCounter = self.tracks[-1].getLastIndex().counter

        for track in other.tracks:
            t = copy.deepcopy(track)
            t.number = track.number + trackCount
            t.session = session
            for i in t.indexes.values():
                if i.absolute is not None:
                    i.absolute += self.leadout + gap
                    logger.debug('Fixing track %02d, index %02d, '
                                 'absolute %d' % (
                                     t.number, i.number, i.absolute))
                if i.counter is not None:
                    i.counter += sourceCounter
                    logger.debug('Fixing track %02d, index %02d, '
                                 'counter %d' % (
                                     t.number, i.number, i.counter))
            self.tracks.append(t)

        self.leadout += other.leadout + gap  # FIXME
        logger.debug('Fixing leadout, now %d', self.leadout)

    def _getSessionGap(self, session):
        # From cdrecord multi-session info:
        # For the first additional session this is 11250 sectors
        # lead-out/lead-in overhead + 150 sectors for the pre-gap of the first
        # track after the lead-in = 11400 sectos.

        # For all further session this is 6750 sectors lead-out/lead-in
        # overhead + 150 sectors for the pre-gap of the first track after the
        # lead-in = 6900 sectors.

        gap = 11400
        if session > 2:
            gap = 6900
        return gap

    # lookups

    def getNextTrackIndex(self, track, index):
        """
        Return the next track and index.

        @param track: track number, 1-based

        @raises IndexError: on last index

        @rtype: tuple of (int, int)
        """
        t = self.tracks[track - 1]
        indexes = t.indexes.keys()
        position = indexes.index(index)

        if position + 1 < len(indexes):
            return track, indexes[position + 1]

        track += 1
        if track > len(self.tracks):
            raise IndexError("No index beyond track %d, index %d" % (
                track - 1, index))

        t = self.tracks[track - 1]
        indexes = t.indexes.keys()

        return track, indexes[0]

    # various tests for types of Table

    def hasTOC(self):
        """
        Check if the Table has a complete TOC.
        a TOC is a list of all tracks and their Index 01, with absolute
        offsets, as well as the leadout.
        """
        if not self.leadout:
            logger.debug('no leadout, no TOC')
            return False

        for t in self.tracks:
            if 1 not in t.indexes.keys():
                logger.debug('no index 1, no TOC')
                return False
            if t.indexes[1].absolute is None:
                logger.debug('no absolute index 1, no TOC')
                return False

        return True

    def accuraterip_ids(self):
        """
        returns both AccurateRip disc ids as a tuple of 8-char
        hexadecimal strings (discid1, discid2)
        """
        # AccurateRip does not take into account data tracks,
        # but does count the data track to determine the leadout offset
        discId1 = 0
        discId2 = 0

        for track in self.tracks:
            if not track.audio:
                continue
            offset = self.getTrackStart(track.number)
            discId1 += offset
            discId2 += (offset or 1) * track.number

        # also add end values, where leadout offset is one past the end
        # of the last track
        offset = self.getTrackEnd(self.tracks[-1].number) + 1
        discId1 += offset
        discId2 += offset * (self.getAudioTracks() + 1)

        discId1 &= 0xffffffff
        discId2 &= 0xffffffff

        return ("%08x" % discId1, "%08x" % discId2)

    def accuraterip_path(self):
        discId1, discId2 = self.accuraterip_ids()
        return "%s/%s/%s/dBAR-%.3d-%s-%s-%s.bin" % (
            discId1[-1], discId1[-2], discId1[-3],
            self.getAudioTracks(), discId1, discId2, self.getCDDBDiscId()
        )

    def canCue(self):
        """
        Check if this table can be used to generate a .cue file
        """
        if not self.hasTOC():
            logger.debug('No TOC, cannot cue')
            return False

        for t in self.tracks:
            for i in t.indexes.values():
                if i.relative is None:
                    logger.debug('Track %02d, Index %02d does not '
                                 'have relative', t.number, i.number)
                    return False

        return True