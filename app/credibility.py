from pathlib import Path
import hashlib, json, time, re
from app.evidence_validation import validate_evidence_file

MAX_EVIDENCE_BYTES = 20 * 1024 * 1024
ALLOWED_EVIDENCE_EXTS = {'.pdf','.jpg','.jpeg','.png','.webp','.xlsx','.xls','.csv','.docx','.txt'}


def _safe_name(name: str) -> str:
    name = Path(str(name or 'evidence.bin')).name
    stem = re.sub(r'[^0-9A-Za-z._\-\u4e00-\u9fff]+', '_', name).strip('._') or 'evidence'
    return stem[:120]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_hash(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256((str(prev_hash or '') + '|' + body).encode('utf-8')).hexdigest()


class EvidenceStore:
    """Local evidence registry with format validation and tamper-evident hash chaining.

    This remains a local integrity mechanism, not a digital-signature/notarization system.
    """
    def __init__(self, db_factory, data_dir: Path, meta_loader):
        self.db_factory = db_factory
        self.data_dir = Path(data_dir)
        self.evidence_dir = self.data_dir / 'evidence'
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.meta_loader = meta_loader

    def save_evidence(self, *, entity_type, entity_id, evidence_type, filename, content: bytes, note='', created_by='',
                      scope_type='item', scope_key='', claimed_values=None, verification=None):
        content = bytes(content or b'')
        if not content:
            raise ValueError('凭证文件为空。')
        if len(content) > MAX_EVIDENCE_BYTES:
            raise ValueError('单个凭证文件超过20MB。')
        ext = Path(str(filename or '')).suffix.lower()
        if ext not in ALLOWED_EVIDENCE_EXTS:
            raise ValueError('凭证文件类型不支持；请使用 PDF/JPG/PNG/WEBP/XLSX/XLS/CSV/DOCX/TXT。')
        fmt = validate_evidence_file(str(filename or ''), content)
        digest = sha256_bytes(content)
        safe = _safe_name(filename)
        stored = f"{time.strftime('%Y%m%d_%H%M%S')}_{digest[:16]}_{safe}"
        path = self.evidence_dir / stored
        if not path.exists():
            path.write_bytes(content)
        created_at = time.strftime('%Y-%m-%d %H:%M:%S')
        meta = self.meta_loader() or {}
        user = str(created_by or meta.get('approver') or '').strip()
        claim = claimed_values or {}
        verify = verification or {}
        payload = {
            'entity_type': str(entity_type or ''), 'entity_id': str(entity_id or ''),
            'evidence_type': str(evidence_type or ''), 'file_name': safe,
            'stored_name': stored, 'sha256': digest, 'note': str(note or ''),
            'created_at': created_at, 'created_by': user, 'format_kind': fmt,
            'scope_type': str(scope_type or 'item'), 'scope_key': str(scope_key or entity_id or ''),
            'claimed_values': claim, 'verification': verify, 'chain_version': 2,
        }
        con = self.db_factory()
        last = con.execute("SELECT chain_hash FROM evidence_files ORDER BY id DESC LIMIT 1").fetchone()
        prev = str(last['chain_hash'] if last and last['chain_hash'] else '')
        chain = canonical_hash(prev, payload)
        cur = con.execute(
            '''INSERT INTO evidence_files(entity_type,entity_id,evidence_type,file_name,stored_name,file_sha256,note,created_at,created_by,prev_hash,chain_hash,
                                          format_kind,scope_type,scope_key,claimed_values_json,verification_json,chain_version)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (payload['entity_type'], payload['entity_id'], payload['evidence_type'], payload['file_name'], payload['stored_name'], digest,
             payload['note'], created_at, user, prev, chain, fmt, payload['scope_type'], payload['scope_key'],
             json.dumps(claim, ensure_ascii=False, sort_keys=True), json.dumps(verify, ensure_ascii=False, sort_keys=True), 2)
        )
        eid = int(cur.lastrowid)
        con.commit(); con.close()
        return {
            'id': eid, 'sha256': digest, 'file_name': safe, 'stored_name': stored, 'chain_hash': chain,
            'created_at': created_at, 'created_by': user, 'format_kind': fmt,
            'scope_type': payload['scope_type'], 'scope_key': payload['scope_key'],
            'claimed_values': claim, 'verification': verify,
        }

    def save_evidence_batch(self, entries):
        """Validate every evidence item first, then register the whole batch atomically.

        A validation or database failure leaves no partial evidence rows. Newly-created
        evidence files are removed on database rollback. This is local transaction
        integrity only; it is not a digital signature or notarization mechanism.
        """
        entries=list(entries or [])
        if not entries:
            return []
        meta=self.meta_loader() or {}
        prepared=[]
        # Phase 1: validate *all* files and metadata before writing anything.
        for e in entries:
            content=bytes(e.get('content') or b'')
            if not content:
                raise ValueError('凭证文件为空。')
            if len(content)>MAX_EVIDENCE_BYTES:
                raise ValueError('单个凭证文件超过20MB。')
            filename=str(e.get('filename') or '')
            ext=Path(filename).suffix.lower()
            if ext not in ALLOWED_EVIDENCE_EXTS:
                raise ValueError('凭证文件类型不支持；请使用 PDF/JPG/PNG/WEBP/XLSX/XLS/CSV/DOCX/TXT。')
            fmt=validate_evidence_file(filename,content)
            digest=sha256_bytes(content); safe=_safe_name(filename)
            stored=f"{time.strftime('%Y%m%d_%H%M%S')}_{digest[:16]}_{safe}"
            created_at=time.strftime('%Y-%m-%d %H:%M:%S')
            user=str(e.get('created_by') or meta.get('approver') or '').strip()
            claim=e.get('claimed_values') or {}; verify=e.get('verification') or {}
            payload={
                'entity_type':str(e.get('entity_type') or ''),'entity_id':str(e.get('entity_id') or ''),
                'evidence_type':str(e.get('evidence_type') or ''),'file_name':safe,'stored_name':stored,
                'sha256':digest,'note':str(e.get('note') or ''),'created_at':created_at,'created_by':user,
                'format_kind':fmt,'scope_type':str(e.get('scope_type') or 'item'),
                'scope_key':str(e.get('scope_key') or e.get('entity_id') or ''),
                'claimed_values':claim,'verification':verify,'chain_version':2,
            }
            prepared.append((payload,content))

        con=self.db_factory(); created_paths=[]; results=[]
        try:
            con.execute('BEGIN')
            last=con.execute("SELECT chain_hash FROM evidence_files ORDER BY id DESC LIMIT 1").fetchone()
            prev=str(last['chain_hash'] if last and last['chain_hash'] else '')
            for payload,content in prepared:
                path=self.evidence_dir/payload['stored_name']
                if not path.exists():
                    path.write_bytes(content); created_paths.append(path)
                chain=canonical_hash(prev,payload)
                cur=con.execute(
                    '''INSERT INTO evidence_files(entity_type,entity_id,evidence_type,file_name,stored_name,file_sha256,note,created_at,created_by,prev_hash,chain_hash,
                                                  format_kind,scope_type,scope_key,claimed_values_json,verification_json,chain_version)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (payload['entity_type'],payload['entity_id'],payload['evidence_type'],payload['file_name'],payload['stored_name'],payload['sha256'],
                     payload['note'],payload['created_at'],payload['created_by'],prev,chain,payload['format_kind'],payload['scope_type'],payload['scope_key'],
                     json.dumps(payload['claimed_values'],ensure_ascii=False,sort_keys=True),
                     json.dumps(payload['verification'],ensure_ascii=False,sort_keys=True),2)
                )
                eid=int(cur.lastrowid)
                results.append({'id':eid,'sha256':payload['sha256'],'file_name':payload['file_name'],'stored_name':payload['stored_name'],
                                'chain_hash':chain,'created_at':payload['created_at'],'created_by':payload['created_by'],'format_kind':payload['format_kind'],
                                'scope_type':payload['scope_type'],'scope_key':payload['scope_key'],
                                'claimed_values':payload['claimed_values'],'verification':payload['verification']})
                prev=chain
            con.commit()
            return results
        except Exception:
            try: con.rollback()
            except Exception: pass
            for path in created_paths:
                try: path.unlink(missing_ok=True)
                except Exception: pass
            raise
        finally:
            con.close()

    def get(self, evidence_id):
        con = self.db_factory()
        row = con.execute('SELECT * FROM evidence_files WHERE id=?', (int(evidence_id),)).fetchone()
        con.close()
        return dict(row) if row else None

    def path_for(self, evidence_id):
        row = self.get(evidence_id)
        if not row:
            return None
        p = self.evidence_dir / str(row['stored_name'])
        return p if p.exists() else None

    def matches(self, evidence_id, *, scope_type=None, scope_key=None, evidence_types=None, entity_types=None, entity_ids=None, require_verified=False):
        row = self.get(evidence_id) if evidence_id else None
        path = self.path_for(evidence_id) if row else None
        if not row or not path:
            return False
        # Release-time evidence validity includes byte integrity, not just database metadata.
        try:
            if sha256_bytes(path.read_bytes()) != str(row.get('file_sha256') or ''):
                return False
        except OSError:
            return False
        if evidence_types and str(row.get('evidence_type') or '') not in set(evidence_types):
            return False
        if entity_types and str(row.get('entity_type') or '') not in set(str(x) for x in entity_types):
            return False
        if entity_ids is not None:
            allowed=set(str(x) for x in (entity_ids if isinstance(entity_ids,(list,tuple,set)) else [entity_ids]))
            if str(row.get('entity_id') or '') not in allowed:
                return False
        if scope_type and str(row.get('scope_type') or '') != str(scope_type):
            return False
        if scope_key is not None and str(row.get('scope_key') or '') != str(scope_key):
            return False
        if require_verified:
            try:
                ver = json.loads(row.get('verification_json') or '{}')
            except Exception:
                ver = {}
            if not bool(ver.get('ok')):
                return False
        return True

    def append_event(self, event_type, entity_type='', entity_id='', entity_name='', details=None):
        meta = self.meta_loader() or {}
        created_at = time.strftime('%Y-%m-%d %H:%M:%S')
        payload = {
            'event_type': str(event_type), 'entity_type': str(entity_type), 'entity_id': str(entity_id), 'entity_name': str(entity_name),
            'project_name': str(meta.get('project_name') or ''), 'approver': str(meta.get('approver') or ''), 'created_at': created_at,
            'details': details or {}
        }
        con = self.db_factory()
        last = con.execute("SELECT chain_hash FROM credibility_events ORDER BY id DESC LIMIT 1").fetchone()
        prev = str(last['chain_hash'] if last and last['chain_hash'] else '')
        chain = canonical_hash(prev, payload)
        con.execute(
            '''INSERT INTO credibility_events(event_type,entity_type,entity_id,entity_name,project_name,approver,created_at,details_json,prev_hash,chain_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (payload['event_type'], payload['entity_type'], payload['entity_id'], payload['entity_name'], payload['project_name'], payload['approver'],
             created_at, json.dumps(payload['details'], ensure_ascii=False, sort_keys=True), prev, chain)
        )
        con.commit(); con.close(); return chain

    def append_release(self, *, export_type, file_name, state_fingerprint, file_sha256, details=None):
        meta = self.meta_loader() or {}
        created_at = time.strftime('%Y-%m-%d %H:%M:%S')
        payload = {
            'export_type': str(export_type), 'file_name': str(file_name), 'state_fingerprint': str(state_fingerprint),
            'project_name': str(meta.get('project_name') or ''), 'approver': str(meta.get('approver') or ''),
            'file_sha256': str(file_sha256 or ''), 'created_at': created_at, 'details': details or {}
        }
        con = self.db_factory()
        last = con.execute("SELECT chain_hash FROM release_audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = str(last['chain_hash'] if last and last['chain_hash'] else '')
        chain = canonical_hash(prev, payload)
        con.execute(
            '''INSERT INTO release_audit_log(export_type,file_name,state_fingerprint,project_name,approver,file_sha256,created_at,details_json,prev_hash,chain_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?)''',
            (payload['export_type'], payload['file_name'], payload['state_fingerprint'], payload['project_name'], payload['approver'],
             payload['file_sha256'], created_at, json.dumps(payload['details'], ensure_ascii=False, sort_keys=True), prev, chain)
        )
        con.commit(); con.close(); return chain

    def verify_chains(self):
        problems = []
        con = self.db_factory()
        for table, cols, details_key in (
            ('evidence_files', ['entity_type','entity_id','evidence_type','file_name','stored_name','file_sha256','note','created_at','created_by'], None),
            ('credibility_events', ['event_type','entity_type','entity_id','entity_name','project_name','approver','created_at'], 'details_json'),
            ('release_audit_log', ['export_type','file_name','state_fingerprint','project_name','approver','file_sha256','created_at'], 'details_json'),
        ):
            rows = con.execute(f'SELECT * FROM {table} ORDER BY id').fetchall(); prev = ''
            for r in rows:
                d = {k:r[k] for k in cols}
                if table == 'evidence_files':
                    if int(r['chain_version'] or 1) >= 2:
                        try: claim = json.loads(r['claimed_values_json'] or '{}')
                        except Exception: claim = {}
                        try: ver = json.loads(r['verification_json'] or '{}')
                        except Exception: ver = {}
                        d = {
                            'entity_type':d['entity_type'],'entity_id':d['entity_id'],'evidence_type':d['evidence_type'],
                            'file_name':d['file_name'],'stored_name':d['stored_name'],'sha256':d['file_sha256'],'note':d['note'],
                            'created_at':d['created_at'],'created_by':d['created_by'],'format_kind':r['format_kind'],
                            'scope_type':r['scope_type'],'scope_key':r['scope_key'],'claimed_values':claim,'verification':ver,'chain_version':2,
                        }
                    else:
                        d = {
                            'entity_type':d['entity_type'],'entity_id':d['entity_id'],'evidence_type':d['evidence_type'],
                            'file_name':d['file_name'],'stored_name':d['stored_name'],'sha256':d['file_sha256'],'note':d['note'],
                            'created_at':d['created_at'],'created_by':d['created_by']
                        }
                elif details_key:
                    try: d['details'] = json.loads(r[details_key] or '{}')
                    except Exception: d['details'] = {}
                expected = canonical_hash(prev, d)
                if str(r['prev_hash'] or '') != prev or str(r['chain_hash'] or '') != expected:
                    problems.append(f'{table} id={r["id"]} 哈希链不一致')
                if table == 'evidence_files':
                    ep = self.evidence_dir / str(r['stored_name'])
                    if not ep.exists():
                        problems.append(f'evidence_files id={r["id"]} 凭证文件缺失')
                    else:
                        try:
                            if sha256_bytes(ep.read_bytes()) != str(r['file_sha256'] or ''):
                                problems.append(f'evidence_files id={r["id"]} 文件SHA-256不一致')
                        except OSError:
                            problems.append(f'evidence_files id={r["id"]} 凭证文件无法读取')
                prev = str(r['chain_hash'] or expected)
        con.close(); return problems
