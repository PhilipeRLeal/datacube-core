import numpy as np

from ..storage.storage import (
    RasterFileDataSource,
    reproject_and_fuse,
)
from ..storage._read import rdr_geobox
from ..utils.geometry import GeoBox


def dc_read(path,
            band=1,
            gbox=None,
            resampling='nearest',
            dtype=None,
            dst_nodata=None,
            fallback_nodata=None):
    """
    Use default io driver to read file without constructing Dataset object.
    """
    source = RasterFileDataSource(path, band, nodata=fallback_nodata)
    with source.open() as rdr:
        dtype = rdr.dtype if dtype is None else dtype
        if gbox is None:
            gbox = rdr_geobox(rdr)
        if dst_nodata is None:
            dst_nodata = rdr.nodata

    # currently dst_nodata = None case is not supported. So if fallback_nodata
    # was None and file had none set, then use 0 as default output fill value
    if dst_nodata is None:
        dst_nodata = 0

    im = np.full(gbox.shape, dst_nodata, dtype=dtype)
    reproject_and_fuse([source], im, gbox, dst_nodata, resampling=resampling)
    return im


def write_gtiff(fname,
                pix,
                crs='epsg:3857',
                resolution=(10, -10),
                offset=(0.0, 0.0),
                nodata=None,
                overwrite=False,
                blocksize=None,
                **extra_rio_opts):
    """ Write ndarray to GeoTiff file.
    """
    # pylint: disable=too-many-locals

    from affine import Affine
    import rasterio
    from pathlib import Path

    if pix.ndim == 2:
        h, w = pix.shape
        nbands = 1
        band = 1
    elif pix.ndim == 3:
        nbands, h, w = pix.shape
        band = tuple(i for i in range(1, nbands+1))
    else:
        raise ValueError('Need 2d or 3d ndarray on input')

    if not isinstance(fname, Path):
        fname = Path(fname)

    if fname.exists():
        if overwrite:
            fname.unlink()
        else:
            raise IOError("File exists")

    sx, sy = resolution
    tx, ty = offset

    A = Affine(sx, 0, tx,
               0, sy, ty)

    rio_opts = dict(width=w,
                    height=h,
                    count=nbands,
                    dtype=pix.dtype.name,
                    crs=crs,
                    transform=A,
                    predictor=2,
                    compress='DEFLATE')

    if blocksize is not None:
        rio_opts.update(tiled=True,
                        blockxsize=min(blocksize, w),
                        blockysize=min(blocksize, h))

    if nodata is not None:
        rio_opts.update(nodata=nodata)

    rio_opts.update(extra_rio_opts)

    with rasterio.open(str(fname), 'w', driver='GTiff', **rio_opts) as dst:
        dst.write(pix, band)
        meta = dst.meta

    meta['gbox'] = rio_geobox(meta)
    meta['path'] = fname
    return meta


def dc_crs_from_rio(crs):
    from datacube.utils.geometry import CRS

    if crs.is_epsg_code:
        return CRS('epsg:{}'.format(crs.to_epsg()))
    return CRS(crs.wkt)


def rio_geobox(meta):
    """ Construct geobox from src.meta of opened rasterio dataset
    """
    if 'crs' not in meta or 'transform' not in meta:
        return None

    h, w = (meta['height'], meta['width'])
    crs = dc_crs_from_rio(meta['crs'])
    transform = meta['transform']

    return GeoBox(w, h, transform, crs)


def rio_slurp_reproject(fname, gbox, dtype=None, dst_nodata=None, **kw):
    """
    Read image with reprojection
    """
    import rasterio
    from rasterio.warp import reproject

    with rasterio.open(str(fname), 'r') as src:
        src_band = rasterio.band(src, 1)

        if dtype is None:
            dtype = src.dtypes[0]
        if dst_nodata is None:
            dst_nodata = src.nodata
        if dst_nodata is None:
            dst_nodata = 0

        pix = np.full(gbox.shape, dst_nodata, dtype=dtype)

        reproject(src_band, pix,
                  dst_nodata=dst_nodata,
                  dst_transform=gbox.transform,
                  dst_crs=str(gbox.crs),
                  **kw)

        meta = src.meta
        meta['src_gbox'] = rio_geobox(meta)
        meta['path'] = fname
        meta['gbox'] = gbox

        return pix, meta


def rio_slurp_read(fname, out_shape=None, **kw):
    """
    Read whole image file using rasterio.

    :returns: ndarray (2d or 3d if multi-band), dict (rasterio meta)
    """
    import rasterio

    if out_shape is not None:
        kw.update(out_shape=out_shape)

    with rasterio.open(str(fname), 'r') as src:
        data = src.read(1, **kw) if src.count == 1 else src.read(**kw)
        meta = src.meta
        meta['gbox'] = rio_geobox(meta)
        meta['path'] = fname
        return data, meta


def rio_slurp(fname, *args, **kw):
    """
    Dispatches to either:

    rio_slurp_read(fname, out_shape, ..)
    rio_slurp_reproject(fname, gbox, ...)

    """
    if len(args) == 0:
        if 'gbox' in kw:
            return rio_slurp_reproject(fname, **kw)
        else:
            return rio_slurp_read(fname, **kw)

    if isinstance(args[0], GeoBox):
        return rio_slurp_reproject(fname, *args, **kw)
    else:
        return rio_slurp_read(fname, *args, **kw)