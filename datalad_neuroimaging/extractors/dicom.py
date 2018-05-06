# emacs: -*- mode: python; py-indent-offset: 4; tab-width: 4; indent-tabs-mode: nil -*-
# ex: set sts=4 ts=4 sw=4 noet:
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
#
#   See COPYING file distributed along with the datalad package for the
#   copyright and license terms.
#
# ## ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ### ##
"""DICOM metadata extractor"""
from __future__ import absolute_import

from six import string_types
from os.path import join as opj, basename
import logging
lgr = logging.getLogger('datalad.metadata.extractors.dicom')
from datalad.log import log_progress

try:
    # renamed for 1.0 release
    import pydicom as dcm
    from pydicom.errors import InvalidDicomError
    from pydicom.dicomdir import DicomDir
except ImportError:  # pragma: no cover
    import dicom as dcm
    from dicom.errors import InvalidDicomError
    from dicom.dicomdir import DicomDir

from datalad.metadata.definitions import vocabulary_id
from datalad.metadata.extractors.base import BaseMetadataExtractor


def _is_good_type(v):
    if isinstance(
            v,
            (int, float, string_types, dcm.valuerep.DSfloat, dcm.valuerep.IS,
             dcm.valuerep.PersonName3)):
        return True
    elif isinstance(v, (list, tuple)):
        return all(map(_is_good_type, v))


def _sanitize_unicode(s):
    return s.replace(u"\u0000", "").strip()


def _convert_value(v):
    t = type(v)
    if t == NoneType:
        cv = v
    elif t in (int, float):
        cv = v
    elif t == str:
        cv = _sanitize_unicode(v)
    elif t == bytes:
        s = v.decode('ascii', 'replace')
        cv = _sanitize_unicode(s)
    elif t == dcm.valuerep.DSfloat:
        cv = float(v)
    elif t == dcm.valuerep.IS:
        cv = int(v)
    elif t == dcm.valuerep.PersonName3:
        cv = str(v)
    elif isinstance(v, (list, tuple)):
        cv = list(map(_convert_value, v))
    else:
        cv = v
    return cv


context = {
    'dicom': {
        # switch to http://dicom.nema.org/resources/ontology/DCM/
        # but requires mapping plain text terms to numbers
        '@id': 'http://semantic-dicom.org/dcm#',
        'description': 'DICOM vocabulary (seemingly incomplete)',
        'type': vocabulary_id}
}


def _struct2dict(struct):
    return {k: _convert_value(getattr(struct, k))
            for k in struct.dir()
            if hasattr(struct, k) and
            _is_good_type(getattr(struct, k))}


class MetadataExtractor(BaseMetadataExtractor):

    _unique_exclude = {
        "AcquisitionTime",
        "ContentTime",
        "InstanceCreationTime",
        "InstanceNumber",
        # this one is actually debatable, if there is a reasonable use case
        # where one would know such a UID and needed to find the dataset with
        # this file, we should keep it in -- but I don't know any ATM
        # and we do still have SeriesInstanceUID
        "SOPInstanceUID",
        "SliceLocation",
        "TemporalPositionIdentifier",
        "TriggerTime",
        "WindowCenter",
        "WindowWidth",
    }

    def get_metadata(self, dataset, content):
        imgseries = {}
        imgs = {}
        log_progress(
            lgr.info,
            'extractordicom',
            'Start DICOM metadata extraction from %s', self.ds,
            total=len(self.paths),
            label='DICOM metadata extraction',
            unit=' Files',
        )
        for f in self.paths:
            absfp = opj(self.ds.path, f)
            log_progress(
                lgr.info,
                'extractordicom',
                'Extract DICOM metadata from %s', absfp,
                update=1,
                increment=True)

            if basename(f).startswith('PSg'):
                # ignore those dicom files, since they appear to not contain
                # any relevant metadata for image series, but causing trouble
                # (see gh-2210). We might want to change that whenever we get
                # a better understanding of how to deal with those files.
                lgr.debug("Ignoring DICOM file %s", f)
                continue

            try:
                d = dcm.read_file(absfp, defer_size="1 KB", stop_before_pixels=True)
            except InvalidDicomError:
                # we can only ignore
                lgr.debug('"%s" does not look like a DICOM file, skipped', f)
                continue

            if isinstance(d, DicomDir):
                lgr.debug("%s appears to be a DICOMDIR file. Extraction not yet"
                          " implemented, skipped", f)
                continue

            ddict = None
            if content:
                ddict = _struct2dict(d)
                imgs[f] = ddict
            if d.SeriesInstanceUID not in imgseries:
                # start with a copy of the metadata of the first dicom in a series
                series = _struct2dict(d) if ddict is None else ddict.copy()
                series_files = []
            else:
                series, series_files = imgseries[d.SeriesInstanceUID]
                # compare incoming with existing metadata set
                series = {
                    k: series[k] for k in series
                    # only keys that exist and have values that are identical
                    # across all images in the series
                    if _convert_value(getattr(d, k, None)) == series[k]
                }
            series_files.append(f)
            # store
            imgseries[d.SeriesInstanceUID] = (series, series_files)
        log_progress(
            lgr.info,
            'extractordicom',
            'Finished DICOM metadata extraction from %s', self.ds
        )

        dsmeta = {
            '@context': context,
            'Series': [info for info, files in imgseries.values()]
        }
        return (
            dsmeta,
            # yield the corresponding series description for each file
            imgs.items() if content else []
        )
