import csv, hashlib, io, os, re, sqlite3, xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
import fitz
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse, HTMLResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
import secrets, time
from pydantic import BaseModel, Field

BASE = Path(__file__).resolve().parent
DB = Path(os.environ.get('GESTOR_DB', str(BASE / 'gestor.db')))
MAX_SIZE = 12 * 1024 * 1024
CHANNELS = ['Shopee', 'Mercado Livre', 'Nuvemshop', 'Amazon', 'Outros', 'Não identificado']
app = FastAPI(title='Justino Tec Bit v1.1')
if not os.getenv('SESSION_SECRET') or not os.getenv('ADMIN_PASSWORD'):
    raise RuntimeError('Defina SESSION_SECRET e ADMIN_PASSWORD antes de iniciar.')
app.add_middleware(SessionMiddleware, secret_key=os.environ['SESSION_SECRET'], https_only=os.getenv('COOKIE_SECURE','true').lower() == 'true', same_site='strict', max_age=8*3600)
FAILED = {}

@app.middleware('http')
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith('/static/') or path in ('/login', '/health'):
        return await call_next(request)
    if not request.session.get('authenticated'):
        if path.startswith('/api/'):
            return JSONResponse({'detail':'Sessão expirada. Faça login.'},status_code=401)
        return RedirectResponse('/login',status_code=303)
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        expected=str(request.base_url).rstrip('/')
        # Render forwards the original scheme in X-Forwarded-Proto.
        if origin and origin != expected:
            return JSONResponse({'detail':'Origem não permitida'},status_code=403)
    return await call_next(request)

@app.get('/health')
def health(): return {'status':'ok'}

@app.get('/login',response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get('authenticated'): return RedirectResponse('/',status_code=303)
    return FileResponse(BASE/'static'/'login.html')

@app.post('/login')
async def login(request:Request, username:str=Form(...), password:str=Form(...)):
    client=request.client.host if request.client else 'unknown'
    now=time.monotonic()
    recent=[t for t in FAILED.get(client,[]) if now-t<900]
    if len(recent)>=8: raise HTTPException(429,'Muitas tentativas. Aguarde 15 minutos.')
    good_user=secrets.compare_digest(username.encode(),os.getenv('ADMIN_USER','admin').encode())
    good_pass=secrets.compare_digest(password.encode(),os.environ['ADMIN_PASSWORD'].encode())
    if not (good_user and good_pass):
        FAILED[client]=(recent+[now])[-8:]
        return RedirectResponse('/login?erro=1',status_code=303)
    FAILED.pop(client,None)
    request.session.clear();request.session['authenticated']=True
    return RedirectResponse('/',status_code=303)

@app.post('/logout')
async def logout(request:Request):
    request.session.clear()
    return RedirectResponse('/login',status_code=303)


@contextmanager
def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, sku TEXT UNIQUE NOT NULL, name TEXT NOT NULL, cost_cents INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS invoices(id INTEGER PRIMARY KEY, access_key TEXT UNIQUE, fingerprint TEXT UNIQUE NOT NULL, number TEXT, issued_at TEXT, marketplace TEXT NOT NULL, source_type TEXT NOT NULL, filename TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), product_id INTEGER REFERENCES products(id), sku TEXT, description TEXT NOT NULL, quantity REAL NOT NULL, unit_cents INTEGER NOT NULL, total_cents INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_items_invoice ON items(invoice_id);
        ''')
init_db()

class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=300)
    cost: float = Field(default=0, ge=0)

def cents(value):
    try:
        return int((Decimal(str(value).replace(',', '.')) * 100).quantize(Decimal('1')))
    except (InvalidOperation, ValueError):
        raise ValueError('Valor monetário inválido')

def localname(tag): return tag.rsplit('}', 1)[-1]
def child(parent, name):
    return next((x for x in parent if localname(x.tag) == name), None) if parent is not None else None
def val(parent, name, default=''):
    x = child(parent, name)
    return (x.text or '').strip() if x is not None else default

def parse_xml(content):
    try: root = ET.fromstring(content)
    except ET.ParseError: raise ValueError('XML inválido')
    inf = next((x for x in root.iter() if localname(x.tag) == 'infNFe'), None)
    if inf is None: raise ValueError('Arquivo não contém uma NF-e (infNFe)')
    ide = child(inf, 'ide')
    access_key = inf.attrib.get('Id', '').replace('NFe', '') or None
    if access_key and not re.fullmatch(r'\d{44}', access_key): access_key = None
    details = [x for x in inf if localname(x.tag) == 'det']
    items = []
    for d in details:
        p = child(d, 'prod')
        if p is None: continue
        qty = float(val(p, 'qCom', '0'))
        total = cents(val(p, 'vProd', '0'))
        unit = cents(val(p, 'vUnCom', '0'))
        if qty < 0: raise ValueError('Quantidade negativa')
        items.append(dict(sku=val(p,'cProd'), description=val(p,'xProd','Sem descrição'), quantity=qty, unit_cents=unit, total_cents=total))
    if not items: raise ValueError('NF-e sem itens comerciais')
    return dict(access_key=access_key, number=val(ide,'nNF'), issued_at=val(ide,'dhEmi') or val(ide,'dEmi'), items=items, status='confirmado_xml')

def parse_pdf(content):
    try:
        with fitz.open(stream=content, filetype='pdf') as doc:
            if doc.page_count > 30: raise ValueError('PDF acima de 30 páginas')
            txt = '\n'.join(page.get_text() for page in doc)
    except ValueError: raise
    except Exception: raise ValueError('PDF inválido')
    if not txt.strip(): raise ValueError('PDF sem texto selecionável; OCR ainda não implementado')
    # PDF varia por emissor; não inferir itens sem confiança.
    match = re.search(r'(?<!\d)(?:\d[\s.]*){44}(?!\d)', txt)
    key = re.sub(r'\D', '', match.group(0)) if match else None
    return dict(access_key=key, number='', issued_at='', items=[], status='revisao_pdf')

@app.get('/')
def home(): return FileResponse(BASE / 'static' / 'index.html')

@app.get('/api/products')
def products():
    with connect() as db: return [dict(x) for x in db.execute('SELECT * FROM products ORDER BY name')]

@app.post('/api/products')
def add_product(p: ProductIn):
    with connect() as db:
        try:
            cur=db.execute('INSERT INTO products(sku,name,cost_cents) VALUES (?,?,?)',(p.sku.strip(),p.name.strip(),cents(p.cost)))
            return {'id':cur.lastrowid}
        except sqlite3.IntegrityError: raise HTTPException(409,'SKU já cadastrado')

@app.post('/api/import')
async def import_files(files: list[UploadFile] = File(...), marketplace: str = Form('Não identificado')):
    if marketplace not in CHANNELS: raise HTTPException(400,'Marketplace inválido')
    results=[]
    for f in files:
        name=Path(f.filename or 'sem_nome').name[:255]
        ext=Path(name).suffix.lower()
        if ext not in ('.xml','.pdf'):
            results.append({'file':name,'status':'erro','detail':'Somente XML e PDF'}); continue
        raw=await f.read(MAX_SIZE+1)
        if len(raw)>MAX_SIZE:
            results.append({'file':name,'status':'erro','detail':'Arquivo maior que 12 MB'}); continue
        fingerprint=hashlib.sha256(raw).hexdigest()
        try:
            data=parse_xml(raw) if ext=='.xml' else parse_pdf(raw)
            with connect() as db:
                duplicate=db.execute('SELECT id FROM invoices WHERE fingerprint=? OR (access_key IS NOT NULL AND access_key=?)',(fingerprint,data['access_key'])).fetchone()
                if duplicate:
                    results.append({'file':name,'status':'duplicado','invoice_id':duplicate['id']}); continue
                cur=db.execute('INSERT INTO invoices(access_key,fingerprint,number,issued_at,marketplace,source_type,filename,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)', (data['access_key'],fingerprint,data['number'],data['issued_at'],marketplace,ext[1:],name,data['status'],datetime.now().isoformat(timespec='seconds')))
                inv_id=cur.lastrowid
                for it in data['items']:
                    prod=db.execute('SELECT id FROM products WHERE sku=?',(it['sku'],)).fetchone()
                    db.execute('INSERT INTO items(invoice_id,product_id,sku,description,quantity,unit_cents,total_cents) VALUES(?,?,?,?,?,?,?)',(inv_id,prod['id'] if prod else None,it['sku'],it['description'],it['quantity'],it['unit_cents'],it['total_cents']))
            results.append({'file':name,'status':data['status'],'invoice_id':inv_id,'items':len(data['items'])})
        except (ValueError,sqlite3.IntegrityError) as exc:
            results.append({'file':name,'status':'erro','detail':str(exc)})
    return {'results':results}

@app.get('/api/invoices')
def invoices():
    with connect() as db:
        return [dict(x) for x in db.execute('''SELECT v.*,COUNT(i.id) item_count,COALESCE(SUM(i.total_cents),0) total_cents FROM invoices v LEFT JOIN items i ON i.invoice_id=v.id GROUP BY v.id ORDER BY v.id DESC''')]

@app.patch('/api/invoices/{invoice_id}/marketplace')
def update_marketplace(invoice_id:int, marketplace:str):
    if marketplace not in CHANNELS: raise HTTPException(400,'Marketplace inválido')
    with connect() as db:
        c=db.execute('UPDATE invoices SET marketplace=? WHERE id=?',(marketplace,invoice_id))
        if not c.rowcount: raise HTTPException(404,'Nota não encontrada')
    return {'ok':True}

@app.get('/api/report')
def report():
    with connect() as db:
        totals=dict(db.execute('SELECT COUNT(*) invoices, SUM(CASE WHEN status="revisao_pdf" THEN 1 ELSE 0 END) pending FROM invoices').fetchone())
        sales=db.execute('SELECT COALESCE(SUM(total_cents),0) revenue, COALESCE(SUM(quantity),0) units FROM items').fetchone()
        channels=[dict(x) for x in db.execute('''SELECT v.marketplace, COUNT(DISTINCT v.id) invoices, COALESCE(SUM(i.total_cents),0) revenue_cents FROM invoices v LEFT JOIN items i ON i.invoice_id=v.id GROUP BY v.marketplace ORDER BY revenue_cents DESC''')]
        by_product=[dict(x) for x in db.execute('''SELECT COALESCE(NULLIF(i.sku,''),'Sem SKU') sku,i.description, v.marketplace, ROUND(SUM(i.quantity),3) quantity,SUM(i.total_cents) revenue_cents, SUM(i.total_cents - ROUND(i.quantity*COALESCE(p.cost_cents,0))) estimated_margin_before_fees_cents FROM items i JOIN invoices v ON v.id=i.invoice_id LEFT JOIN products p ON p.id=i.product_id GROUP BY i.sku,i.description,v.marketplace ORDER BY quantity DESC''')]
        return {'invoices':totals['invoices'],'pending_pdf':totals['pending'] or 0,'revenue_cents':sales['revenue'],'units':sales['units'],'channels':channels,'products':by_product}

@app.get('/api/report.csv')
def report_csv():
    data=report()['products']; out=io.StringIO(); out.write('\ufeff')
    writer=csv.writer(out,delimiter=';');writer.writerow(['SKU','Produto','Marketplace','Quantidade','Faturamento (R$)','Margem estimada antes das taxas (R$)'])
    for x in data: writer.writerow([x['sku'],x['description'],x['marketplace'],x['quantity'],f"{x['revenue_cents']/100:.2f}".replace('.',','),f"{x['estimated_margin_before_fees_cents']/100:.2f}".replace('.',',')])
    return StreamingResponse(iter([out.getvalue().encode('utf-8')]),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="relatorio_vendas.csv"'})
