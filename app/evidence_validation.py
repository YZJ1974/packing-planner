from __future__ import annotations
from pathlib import Path
import io, zipfile, math

ALLOWED_EXTS={'.pdf','.jpg','.jpeg','.png','.webp','.xlsx','.xls','.csv','.docx','.txt'}


def _decode_text(data: bytes):
    for enc in ('utf-8-sig','utf-8','gb18030','latin1'):
        try:
            return data.decode(enc)
        except Exception:
            pass
    return None


def validate_evidence_file(filename: str, content: bytes) -> str:
    """Validate that bytes structurally match the declared extension.

    This is format validation only; it does not prove that the document content supports
    a measurement or weight value.
    """
    data=bytes(content or b'')
    if not data:
        raise ValueError('凭证文件为空。')
    ext=Path(str(filename or '')).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError('凭证文件类型不支持；请使用 PDF/JPG/PNG/WEBP/XLSX/XLS/CSV/DOCX/TXT。')
    if ext=='.pdf':
        if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-4096:]:
            raise ValueError('PDF凭证格式无效或文件不完整。')
        return 'pdf'
    if ext in {'.jpg','.jpeg'}:
        if len(data)<4 or not data.startswith(b'\xff\xd8\xff') or not data.rstrip().endswith(b'\xff\xd9'):
            raise ValueError('JPEG凭证格式无效或文件不完整。')
        return 'jpeg'
    if ext=='.png':
        if not data.startswith(b'\x89PNG\r\n\x1a\n') or b'IEND' not in data[-64:]:
            raise ValueError('PNG凭证格式无效或文件不完整。')
        return 'png'
    if ext=='.webp':
        if len(data)<12 or data[:4]!=b'RIFF' or data[8:12]!=b'WEBP':
            raise ValueError('WEBP凭证格式无效。')
        return 'webp'
    if ext=='.xls':
        if not data.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
            raise ValueError('XLS凭证不是有效的OLE复合文档。')
        return 'xls'
    if ext in {'.xlsx','.docx'}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names=set(z.namelist())
                if '[Content_Types].xml' not in names:
                    raise ValueError('Office凭证缺少Content Types。')
                if ext=='.xlsx' and 'xl/workbook.xml' not in names:
                    raise ValueError('XLSX凭证缺少工作簿结构。')
                if ext=='.docx' and 'word/document.xml' not in names:
                    raise ValueError('DOCX凭证缺少正文结构。')
        except zipfile.BadZipFile as e:
            raise ValueError('Office凭证ZIP结构无效。') from e
        return ext[1:]
    text=_decode_text(data)
    if text is None or not text.strip():
        raise ValueError('文本凭证为空或无法解码。')
    printable=sum(1 for ch in text[:10000] if ch.isprintable() or ch in '\r\n\t')
    if printable/max(1,min(len(text),10000))<0.85:
        raise ValueError('文本凭证包含过多不可读字符。')
    return ext[1:]


def numeric_close(system_value, evidence_value, *, abs_tol=0.0, rel_tol=0.001):
    try:
        a=float(system_value); b=float(evidence_value)
    except Exception:
        return False, None
    if not math.isfinite(a) or not math.isfinite(b):
        return False, None
    tol=max(float(abs_tol),abs(a)*float(rel_tol))
    return abs(a-b)<=tol, tol


def verify_dimension_claim(system_dims, evidence_dims):
    labels=('L','W','H'); problems=[]; tolerances={}
    for k,a,b in zip(labels,system_dims,evidence_dims):
        ok,tol=numeric_close(a,b,abs_tol=1.0,rel_tol=0.001); tolerances[k]=tol
        if not ok: problems.append(f'{k}：系统 {float(a):g} mm / 凭证核对值 {float(b):g} mm')
    return {'ok':not problems,'problems':problems,'tolerances_mm':tolerances,
            'system_mm':[float(x) for x in system_dims],'evidence_mm':[float(x) for x in evidence_dims]}


def verify_weight_claim(system_kg, evidence_kg):
    ok,tol=numeric_close(system_kg,evidence_kg,abs_tol=0.1,rel_tol=0.001)
    return {'ok':ok,'problems':[] if ok else [f'系统 {float(system_kg):g} kg / 凭证核对值 {float(evidence_kg):g} kg'],
            'tolerance_kg':tol,'system_kg':float(system_kg),'evidence_kg':float(evidence_kg)}
