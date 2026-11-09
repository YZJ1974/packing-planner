import io, zipfile

MAX_ZIP_MEMBERS=5000
MAX_ZIP_TOTAL_UNCOMPRESSED=100*1024*1024
MAX_ZIP_MEMBER_UNCOMPRESSED=30*1024*1024
MAX_ZIP_RATIO=250.0


def validate_zip_archive(content: bytes, label='压缩文件'):
    """Validate ZIP metadata and actual streamed decompression volume.

    The streamed read is deliberate: central-directory file_size alone is not accepted as
    the memory-safety boundary. We never retain decompressed members here.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            infos=z.infolist()
            if len(infos)>MAX_ZIP_MEMBERS:
                raise ValueError(f'{label}内部文件数量过多。')
            meta_total=0; actual_total=0
            for info in infos:
                if info.is_dir(): continue
                meta_size=int(info.file_size or 0); comp=int(info.compress_size or 0)
                meta_total += meta_size
                if meta_size>MAX_ZIP_MEMBER_UNCOMPRESSED:
                    raise ValueError(f'{label}内部成员过大：{info.filename}')
                if meta_size>1024*1024 and meta_size/max(1,comp)>MAX_ZIP_RATIO:
                    raise ValueError(f'{label}压缩比异常：{info.filename}')
                if meta_total>MAX_ZIP_TOTAL_UNCOMPRESSED:
                    raise ValueError(f'{label}解压后总大小超过100MB。')
                member_actual=0
                with z.open(info,'r') as src:
                    while True:
                        chunk=src.read(1024*1024)
                        if not chunk: break
                        member_actual += len(chunk); actual_total += len(chunk)
                        if member_actual>MAX_ZIP_MEMBER_UNCOMPRESSED:
                            raise ValueError(f'{label}实际解压成员超过30MB：{info.filename}')
                        if actual_total>MAX_ZIP_TOTAL_UNCOMPRESSED:
                            raise ValueError(f'{label}实际解压总量超过100MB。')
                if member_actual>1024*1024 and member_actual/max(1,comp)>MAX_ZIP_RATIO:
                    raise ValueError(f'{label}实际解压压缩比异常：{info.filename}')
            return infos
    except zipfile.BadZipFile as e:
        raise ValueError(f'{label}不是有效的ZIP/OOXML文件。') from e
