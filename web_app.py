"""Cloud Drive Web UI — browse and monitor your S3 backup."""

import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import boto3
import streamlit as st
import streamlit.components.v1 as components
import yaml

st.set_page_config(page_title="Cloud Drive", page_icon="☁️", layout="wide")

# ── config ────────────────────────────────────────────────────────────────────

CONFIG_SEARCH = [
    Path(__file__).parent / "config.yaml",
    Path.home() / ".cloud-drive" / "config.yaml",
]

INDEX_DB = Path(os.environ.get("CLOUD_DRIVE_INDEX", "~/.cloud-drive/index.db")).expanduser()
LOG_FILE = Path(__file__).parent / "backup.log"
BUCKET   = os.environ.get("CLOUD_DRIVE_BUCKET")
PREFIX   = os.environ.get("CLOUD_DRIVE_PREFIX")
REGION   = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
API_URL  = os.environ.get("CLOUD_DRIVE_API_URL", "http://localhost:8506")


def _load_config() -> dict:
    cfg = {"bucket": BUCKET, "s3_prefix": PREFIX or "seagate/Personal", "region": REGION}
    for p in CONFIG_SEARCH:
        if p.exists():
            with open(p) as f:
                overrides = yaml.safe_load(f) or {}
            cfg.update(overrides)
            break
    if BUCKET:  cfg["bucket"] = BUCKET
    if PREFIX:  cfg["s3_prefix"] = PREFIX
    return cfg


CFG = _load_config()

# ── helpers ───────────────────────────────────────────────────────────────────

def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _has_index() -> bool:
    return INDEX_DB.exists()


# ── tree ──────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=120, show_spinner="Building folder tree…")
def _build_tree() -> dict:
    prefix = CFG["s3_prefix"].rstrip("/") + "/"

    if _has_index():
        conn  = sqlite3.connect(INDEX_DB)
        rows  = conn.execute("SELECT s3_key, size, synced_at FROM files").fetchall()
        conn.close()
        items = [(r[0], r[1], (r[2] or "")[:10]) for r in rows]
    else:
        try:
            client    = boto3.client("s3", region_name=CFG["region"])
            paginator = client.get_paginator("list_objects_v2")
            items     = []
            for page in paginator.paginate(Bucket=CFG["bucket"], Prefix=prefix):
                for obj in page.get("Contents", []):
                    items.append((obj["Key"], obj["Size"],
                                  obj["LastModified"].strftime("%Y-%m-%d")))
        except Exception as exc:
            st.error(f"Could not reach S3: {exc}")
            return {}

    tree: dict = {}

    def node(path: str) -> dict:
        if path not in tree:
            tree[path] = {"dirs": {}, "files": []}
        return tree[path]

    for key, size, synced in items:
        rel    = key[len(prefix):] if key.startswith(prefix) else key
        if not rel:
            continue
        parts  = rel.rstrip("/").split("/")
        fname  = parts[-1]
        fparts = parts[:-1]
        fpath  = "/".join(fparts)

        node(fpath)["files"].append({"n": fname, "s": size, "d": synced, "k": key})

        for i in range(len(fparts)):
            parent     = "/".join(fparts[:i]) if i > 0 else ""
            child      = fparts[i]
            d          = node(parent)["dirs"]
            if child not in d:
                d[child] = {"b": 0, "c": 0}
            d[child]["b"] += size
            d[child]["c"] += 1
            node("/".join(fparts[:i + 1]))

    return tree


@st.cache_data(ttl=120)
def _tree_json() -> str:
    return json.dumps(_build_tree(), separators=(",", ":"))


# ── overview helpers ──────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def _stats_from_index() -> dict:
    conn = sqlite3.connect(INDEX_DB)
    conn.row_factory = sqlite3.Row
    row  = conn.execute("SELECT COUNT(*) as c, SUM(size) as s FROM files").fetchone()
    last = conn.execute("SELECT synced_at FROM files ORDER BY synced_at DESC LIMIT 1").fetchone()
    conn.close()
    return {"count": row["c"] or 0, "total_bytes": row["s"] or 0,
            "last_sync": last["synced_at"] if last else None}


@st.cache_data(ttl=120)
def _folders_from_index() -> list[dict]:
    conn   = sqlite3.connect(INDEX_DB)
    rows   = conn.execute("SELECT s3_key, size FROM files").fetchall()
    conn.close()
    prefix = CFG["s3_prefix"].rstrip("/") + "/"
    acc: dict[str, dict] = defaultdict(lambda: {"count": 0, "bytes": 0})
    for r in rows:
        rel = r[0][len(prefix):] if r[0].startswith(prefix) else r[0]
        top = rel.split("/")[0] if "/" in rel else "(root)"
        acc[top]["count"] += 1
        acc[top]["bytes"] += r[1]
    return sorted([{"folder": k, **v} for k, v in acc.items()],
                  key=lambda x: x["bytes"], reverse=True)


# ── log parser ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def _parse_log() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    entries = []
    for block in re.split(r"={20,}", LOG_FILE.read_text(errors="replace")):
        m = re.search(r"Cloud-drive sync (.+?) →", block)
        if not m:
            continue
        folder   = m.group(1).strip()
        uploaded = re.search(r"Uploaded (\d+) files \(([^)]+)\)", block)
        finished = re.search(r"Finished: (.+)", block)
        skipped  = re.search(r"Skipped (\d+)", block)
        failed   = re.search(r"Failed (\d+)", block)
        if uploaded or finished:
            entries.append({
                "folder":    Path(folder).name,
                "full_path": folder,
                "uploaded":  int(uploaded.group(1)) if uploaded else "—",
                "size":      uploaded.group(2) if uploaded else "—",
                "skipped":   int(skipped.group(1)) if skipped else 0,
                "failed":    int(failed.group(1)) if failed else 0,
                "finished":  finished.group(1).strip() if finished else "running…",
                "done":      finished is not None,
            })
    return list(reversed(entries))


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("☁️ Cloud Drive")
    st.caption(f"**Bucket:** `{CFG['bucket']}`")
    st.caption(f"**Prefix:** `{CFG['s3_prefix']}`")
    st.caption(f"**Storage:** `{CFG.get('default_storage_class', 'GLACIER_IR')}`")
    st.caption(f"**Region:** `{CFG.get('region', 'us-east-1')}`")
    st.caption(f"**Source:** {'local index' if _has_index() else 'S3'}")
    st.divider()
    if st.button("🔄 Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── tabs ──────────────────────────────────────────────────────────────────────

tab_overview, tab_browse, tab_log = st.tabs(["📊 Overview", "📂 Browse", "📋 Sync Log"])

# ── Overview ──────────────────────────────────────────────────────────────────

with tab_overview:
    if _has_index():
        stats   = _stats_from_index()
        folders = _folders_from_index()
    else:
        stats   = {"count": 0, "total_bytes": 0, "last_sync": None}
        folders = []

    st.header("Backup Overview")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Files Backed Up", f"{stats['count']:,}")
    c2.metric("Total Size", _human(stats["total_bytes"]))
    c3.metric("Folders", str(len(folders)))
    last = stats["last_sync"] or "—"
    c4.metric("Last Sync", last[:10] if last != "—" else "—",
              last[11:16] if last != "—" else None, delta_color="off")

    if folders:
        st.divider()
        st.subheader("Folder Breakdown")
        total_bytes = stats["total_bytes"] or 1
        for f in folders:
            col_name, col_files, col_size, col_bar = st.columns([3, 1, 1, 3])
            col_name.write(f"**{f['folder']}**")
            col_files.write(f"{f['count']:,} files")
            col_size.write(_human(f["bytes"]))
            col_bar.progress(f["bytes"] / total_bytes)

# ── Browse ────────────────────────────────────────────────────────────────────

with tab_browse:
    tree_data = _tree_json()

    EXPLORER_HTML = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;overflow:hidden}}
body{{display:flex;background:#181818;color:#ccc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;font-size:14px}}

/* ── Rail ── */
#rail{{width:260px;min-width:150px;max-width:600px;background:#101010;border-right:1px solid #1c1c1c;overflow-y:auto;display:flex;flex-direction:column;flex-shrink:0}}
#rail-header{{font-size:.6rem;text-transform:uppercase;letter-spacing:.12em;color:#f97316;padding:.7rem .8rem .3rem;font-weight:700;flex-shrink:0}}
#rail-search{{display:block;width:calc(100% - 1rem);margin:.2rem .5rem .3rem;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:5px;color:#ccc;font-size:.75rem;padding:.3rem .5rem;outline:none;flex-shrink:0}}
#rail-search:focus{{border-color:#f97316}}
#tree{{flex:1;overflow-y:auto;padding-bottom:.5rem}}

/* ── Tree rows ── */
.tree-row{{display:flex;align-items:center;gap:3px;padding:2px 6px 2px 0;border-radius:4px;cursor:pointer;transition:background .1s;user-select:none;outline:none}}
.tree-row:hover{{background:rgba(255,255,255,.06)}}
.tree-row.active{{background:rgba(255,0,132,.18)}}
.tree-row.active .tree-label{{color:#ffd0ea;font-weight:600}}
.tree-row:hover .tree-label{{color:#e2e8f0}}
.tree-arrow{{flex-shrink:0;width:14px;font-size:.6rem;color:#555;text-align:center;visibility:hidden}}
.tree-arrow.vis{{visibility:visible}}
.tree-row:hover .tree-arrow{{color:#888}}
.tree-icon{{flex-shrink:0;font-size:.78rem;line-height:1}}
.tree-label{{font-size:.78rem;color:#aaa;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}}
.tree-count{{font-size:.62rem;color:#444;flex-shrink:0;margin-left:2px}}

/* ── Resize handle ── */
#resize{{width:5px;cursor:col-resize;background:transparent;flex-shrink:0;transition:background .15s;z-index:10}}
#resize:hover,#resize.drag{{background:#f97316}}

/* ── Main panel ── */
#main{{flex:1;display:flex;flex-direction:column;min-width:0;overflow:hidden}}
#topbar{{display:flex;align-items:center;gap:.4rem;padding:.6rem .8rem .4rem;border-bottom:1px solid #1c1c1c;flex-shrink:0;flex-wrap:wrap}}
.crumb-btn{{background:none;border:none;color:#f97316;cursor:pointer;font-size:.8rem;padding:.1rem .25rem;border-radius:3px}}
.crumb-btn:hover{{background:rgba(249,115,22,.12)}}
.crumb-sep{{color:#333;font-size:.8rem}}
#search-bar{{flex:1;min-width:120px;max-width:280px;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:5px;color:#ccc;font-size:.78rem;padding:.3rem .5rem;outline:none}}
#search-bar:focus{{border-color:#f97316}}
#content{{flex:1;overflow-y:auto;padding:.6rem .8rem}}

/* ── Section labels ── */
.sec-label{{font-size:.62rem;text-transform:uppercase;letter-spacing:.1em;color:#555;padding:.5rem 0 .25rem;font-weight:600}}

/* ── Folder rows ── */
.folder-row{{display:flex;align-items:center;gap:.6rem;padding:.4rem .5rem;border-radius:5px;cursor:pointer;transition:background .1s;border-bottom:1px solid #141414}}
.folder-row:hover{{background:rgba(255,255,255,.05)}}
.folder-row:hover .fr-name{{color:#fff}}
.fr-icon{{font-size:.95rem;flex-shrink:0}}
.fr-name{{font-size:.85rem;color:#ddd;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.fr-meta{{font-size:.72rem;color:#555;flex-shrink:0;text-align:right;white-space:nowrap}}

/* ── File table ── */
.file-table{{width:100%;border-collapse:collapse;margin-top:.25rem}}
.file-table th{{font-size:.62rem;text-transform:uppercase;letter-spacing:.08em;color:#555;padding:.35rem .5rem;border-bottom:1px solid #1c1c1c;text-align:left;font-weight:600;position:sticky;top:0;background:#181818;white-space:nowrap}}
.file-table td{{font-size:.78rem;color:#999;padding:.28rem .5rem;border-bottom:1px solid #111}}
.file-table tr:hover td{{background:rgba(255,255,255,.03);color:#ccc}}
.td-name{{max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#bbb}}
.td-act{{width:32px;text-align:center;padding:.2rem .3rem !important}}
.act-btn{{background:none;border:none;cursor:pointer;font-size:.88rem;padding:.15rem .3rem;border-radius:4px;color:#666;transition:color .15s,background .15s;line-height:1}}
.act-btn:hover{{color:#f97316;background:rgba(249,115,22,.12)}}
.act-btn.spin{{animation:spin .6s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}

/* ── Pagination ── */
.pager{{display:flex;align-items:center;gap:.5rem;padding:.6rem 0}}
.pg-btn{{background:#1a1a1a;border:1px solid #2a2a2a;color:#888;padding:.25rem .7rem;border-radius:4px;cursor:pointer;font-size:.75rem}}
.pg-btn:hover{{background:#222;color:#ccc}}
.pg-btn:disabled{{opacity:.35;cursor:default}}
.pg-info{{font-size:.72rem;color:#555}}
.empty{{color:#444;font-size:.82rem;padding:1rem 0}}

/* ── Modal ── */
#modal{{display:none;position:fixed;inset:0;z-index:1000;align-items:center;justify-content:center;background:rgba(0,0,0,.88)}}
#modal.open{{display:flex}}
#modal-box{{background:#1a1a1a;border-radius:8px;border:1px solid #2a2a2a;max-width:92vw;max-height:92vh;width:900px;display:flex;flex-direction:column;overflow:hidden}}
#modal-header{{display:flex;align-items:center;gap:.6rem;padding:.6rem .9rem;border-bottom:1px solid #2a2a2a;flex-shrink:0;background:#111}}
#modal-title{{flex:1;font-size:.82rem;color:#ddd;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.modal-btn{{background:#252525;border:1px solid #333;color:#aaa;padding:.25rem .65rem;border-radius:4px;cursor:pointer;font-size:.78rem;white-space:nowrap}}
.modal-btn:hover{{background:#2e2e2e;color:#eee}}
#modal-body{{flex:1;overflow:auto;display:flex;align-items:center;justify-content:center;background:#111;min-height:200px}}
#modal-body img{{max-width:100%;max-height:78vh;object-fit:contain;display:block}}
#modal-body video{{max-width:100%;max-height:78vh}}
#modal-body audio{{width:90%;margin:2rem}}
#modal-body iframe{{width:100%;height:78vh;border:none}}
.modal-msg{{color:#666;font-size:.85rem;padding:2rem;text-align:center;line-height:1.8}}
.modal-loading{{color:#555;font-size:.82rem}}
</style>
</head>
<body>

<div id="rail">
  <div id="rail-header">📁 Folders</div>
  <input id="rail-search" type="text" placeholder="Filter folders…">
  <div id="tree"></div>
</div>

<div id="resize"></div>

<div id="main">
  <div id="topbar">
    <div id="breadcrumb" style="display:flex;align-items:center;gap:.3rem;flex-wrap:wrap;flex:1"></div>
    <input id="search-bar" type="text" placeholder="🔍 Search files…">
  </div>
  <div id="content"></div>
</div>

<!-- Preview modal -->
<div id="modal">
  <div id="modal-box">
    <div id="modal-header">
      <span id="modal-title"></span>
      <button class="modal-btn" id="modal-dl">⬇ Download</button>
      <button class="modal-btn" id="modal-close">✕ Close</button>
    </div>
    <div id="modal-body"></div>
  </div>
</div>

<script>
const TREE    = {tree_data};
const API     = '{API_URL}';
const IMG_EXT = new Set(['jpg','jpeg','png','gif','webp','bmp','svg','avif','ico','tiff','tif']);
const VID_EXT = new Set(['mp4','mov','m4v','webm','mkv','avi','3gp','ogv']);
const AUD_EXT = new Set(['mp3','wav','m4a','ogg','flac','aac','opus','wma']);
const PDF_EXT = new Set(['pdf']);
const TXT_EXT = new Set(['txt','md','json','xml','csv','log','yaml','yml','ini','toml','sh','bash','py','js','ts','jsx','tsx','html','css','scss','sql','conf','cfg','env','gitignore','dockerfile','makefile','rs','go','java','c','cpp','h','rb','php']);

const state = {{ path:[], expanded:new Set(), search:'', folderFilter:'', page:0, PAGE:200 }};

function hu(n) {{
  const u=['B','KB','MB','GB','TB'];
  for(const x of u){{if(n<1024)return n.toFixed(1)+' '+x;n/=1024;}}
  return n.toFixed(1)+' PB';
}}
function ps(parts){{return parts.join('/');}}
function getNode(parts){{return TREE[ps(parts)]??{{dirs:{{}},files:[]}};}}
function sortedKeys(obj){{return Object.keys(obj).sort((a,b)=>a.toLowerCase().localeCompare(b.toLowerCase()));}}
function getExt(name){{const i=name.lastIndexOf('.');return i>=0?name.slice(i+1).toLowerCase():'';}}
function esc(s){{return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}

// ── API ─────────────────────────────────────────────────────────────────────
async function presign(key, dl=false) {{
  const url = `${{API}}/presign?key=${{encodeURIComponent(key)}}${{dl?'&dl=1':''}}`;
  const r = await fetch(url);
  const d = await r.json();
  if(d.error) throw new Error(d.error);
  return d.url;
}}

// ── Modal ────────────────────────────────────────────────────────────────────
const modal = document.getElementById('modal');
document.getElementById('modal-close').onclick = closeModal;
document.getElementById('modal-dl').onclick = () => {{}};
modal.addEventListener('click', e => {{ if(e.target===modal) closeModal(); }});
document.addEventListener('keydown', e => {{ if(e.key==='Escape') closeModal(); }});

function closeModal() {{
  const v=modal.querySelector('video'), a=modal.querySelector('audio');
  if(v) v.pause(); if(a) a.pause();
  document.getElementById('modal-body').innerHTML='';
  modal.classList.remove('open');
}}

function openModal(title, bodyHtml, dlFn=null) {{
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = bodyHtml;
  const dlBtn = document.getElementById('modal-dl');
  if(dlFn){{ dlBtn.style.display=''; dlBtn.onclick=dlFn; }}
  else {{ dlBtn.style.display='none'; }}
  modal.classList.add('open');
}}

async function viewFile(key, name) {{
  openModal(name, '<div class="modal-msg modal-loading">⏳ Generating secure link…</div>', null);
  try {{
    const ext = getExt(name);
    const url = await presign(key);
    const dlFn = async () => {{ window.open(await presign(key, true), '_blank'); }};
    let body='';
    if(IMG_EXT.has(ext)) {{
      body=`<img src="${{esc(url)}}" alt="${{esc(name)}}">`;
    }} else if(VID_EXT.has(ext)) {{
      body=`<video src="${{esc(url)}}" controls autoplay></video>`;
    }} else if(AUD_EXT.has(ext)) {{
      body=`<audio src="${{esc(url)}}" controls autoplay></audio>`;
    }} else if(PDF_EXT.has(ext)) {{
      body=`<iframe src="${{esc(url)}}"></iframe>`;
    }} else if(TXT_EXT.has(ext)) {{
      openModal(name, '<div class="modal-msg modal-loading">⏳ Loading…</div>', async()=>{{window.open(await presign(key,true),'_blank');}});
      const r2 = await fetch(`${{API}}/content?key=${{encodeURIComponent(key)}}`);
      const txt = await r2.text();
      const pre = document.createElement('pre');
      pre.style.cssText='margin:0;padding:1rem;font-size:.78rem;color:#ccc;white-space:pre-wrap;word-break:break-word;width:100%;box-sizing:border-box;text-align:left;max-height:78vh;overflow:auto;background:#111;font-family:ui-monospace,monospace';
      pre.textContent=txt;
      document.getElementById('modal-body').innerHTML='';
      document.getElementById('modal-body').appendChild(pre);
      return;
    }} else {{
      body=`<div class="modal-msg">No preview for <strong>.${{ext||'unknown'}}</strong> files.<br><br>Use ⬇ Download to save it.</div>`;
    }}
    openModal(name, body, dlFn);
  }} catch(e) {{
    openModal('Error', `<div class="modal-msg" style="color:#f87171">${{esc(e.message)}}</div>`);
  }}
}}

async function downloadFile(key, name, btn) {{
  btn.textContent='⏳'; btn.disabled=true;
  try {{
    const url = await presign(key, true);
    const a = document.createElement('a');
    a.href=url; a.download=name; a.target='_blank';
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
  }} catch(e) {{
    alert('Download failed: '+e.message);
  }} finally {{
    btn.textContent='⬇'; btn.disabled=false;
  }}
}}

// ── Navigation ───────────────────────────────────────────────────────────────
function nav(parts) {{
  state.path=[...parts]; state.page=0; state.search='';
  document.getElementById('search-bar').value='';
  // expand every ancestor so the path is visible in the tree
  for(let i=1;i<=parts.length;i++) state.expanded.add(parts.slice(0,i).join('/'));
  render();
}}

// ── Tree ─────────────────────────────────────────────────────────────────────
function mkTreeNode(parentPath, name, depth) {{
  const fullPath = parentPath ? parentPath+'/'+name : name;
  const node     = TREE[fullPath]??{{dirs:{{}},files:[]}};
  const hasKids  = Object.keys(node.dirs).length>0;
  const expanded = state.expanded.has(fullPath);
  const isActive = ps(state.path)===fullPath;

  const wrap=document.createElement('div');
  const row=document.createElement('div');
  row.className='tree-row'+(isActive?' active':'');
  row.style.paddingLeft=(depth*14+6)+'px';
  row.tabIndex=0;

  const arrow=document.createElement('span');
  arrow.className='tree-arrow'+(hasKids?' vis':'');
  arrow.textContent=hasKids?(expanded?'▾':'▸'):'';

  const icon=document.createElement('span');
  icon.className='tree-icon'; icon.textContent='📁';

  const label=document.createElement('span');
  label.className='tree-label'; label.textContent=name;

  const pNode=TREE[parentPath]??TREE['']??{{dirs:{{}}}};
  const info=pNode.dirs[name]??{{}};
  const cnt=document.createElement('span');
  cnt.className='tree-count';
  cnt.textContent=info.c?info.c.toLocaleString():'';

  row.appendChild(arrow); row.appendChild(icon);
  row.appendChild(label); row.appendChild(cnt);

  row.addEventListener('click',()=>{{
    if(hasKids){{
      if(state.expanded.has(fullPath)) state.expanded.delete(fullPath);
      else state.expanded.add(fullPath);
    }}
    // set path and expand only ancestor nodes, not this node itself
    state.path=[...fullPath.split('/')]; state.page=0; state.search='';
    document.getElementById('search-bar').value='';
    const segs=fullPath.split('/');
    for(let i=1;i<segs.length;i++) state.expanded.add(segs.slice(0,i).join('/'));
    render();
  }});
  row.addEventListener('keydown',e=>{{if(e.key==='Enter'||e.key===' '){{e.preventDefault();row.click();}}}});
  wrap.appendChild(row);

  if(expanded&&hasKids){{
    for(const k of sortedKeys(node.dirs)) wrap.appendChild(mkTreeNode(fullPath,k,depth+1));
  }}
  return wrap;
}}

function renderTree() {{
  const el=document.getElementById('tree'); el.innerHTML='';
  const root=TREE['']??{{dirs:{{}}}};
  const q=state.folderFilter.toLowerCase();
  let keys=sortedKeys(root.dirs);
  if(q) keys=keys.filter(k=>k.toLowerCase().includes(q));
  for(const k of keys) el.appendChild(mkTreeNode('',k,0));
}}

// ── Breadcrumb ────────────────────────────────────────────────────────────────
function renderBreadcrumb() {{
  const el=document.getElementById('breadcrumb'); el.innerHTML='';
  const root=document.createElement('button');
  root.className='crumb-btn'; root.textContent='🏠 Root';
  root.addEventListener('click',()=>nav([]));
  el.appendChild(root);
  state.path.forEach((seg,i)=>{{
    const sep=document.createElement('span');
    sep.className='crumb-sep'; sep.textContent='›'; el.appendChild(sep);
    const btn=document.createElement('button');
    btn.className='crumb-btn'; btn.textContent=seg;
    btn.addEventListener('click',()=>nav(state.path.slice(0,i+1)));
    el.appendChild(btn);
  }});
}}

// ── Content ───────────────────────────────────────────────────────────────────
function renderContent() {{
  const el=document.getElementById('content'); el.innerHTML='';
  const node=getNode(state.path);
  const subdirs=sortedKeys(node.dirs);
  const q=state.search.toLowerCase();
  const allFiles=node.files??[];
  const files=q?allFiles.filter(f=>f.n.toLowerCase().includes(q)):allFiles;

  // folders
  if(subdirs.length){{
    const lbl=document.createElement('div');
    lbl.className='sec-label';
    lbl.textContent=subdirs.length+' folder'+(subdirs.length!==1?'s':'');
    el.appendChild(lbl);
    for(const name of subdirs){{
      const info=node.dirs[name]??{{}};
      const row=document.createElement('div');
      row.className='folder-row';
      row.innerHTML=`<span class="fr-icon">📁</span>
        <span class="fr-name">${{esc(name)}}</span>
        <span class="fr-meta">${{info.c?info.c.toLocaleString()+' items':''}}${{info.b?' &nbsp;·&nbsp; '+hu(info.b):''}}</span>`;
      row.addEventListener('click',()=>{{
        const np=[...state.path,name];
        state.expanded.add(ps(np));
        nav(np);
      }});
      el.appendChild(row);
    }}
  }}

  // files
  if(files.length){{
    const lbl=document.createElement('div');
    lbl.className='sec-label';
    lbl.style.marginTop=subdirs.length?'1rem':'0';
    lbl.textContent=files.length.toLocaleString()+' file'+(files.length!==1?'s':'')+
      (q&&files.length!==allFiles.length?' (filtered from '+allFiles.length.toLocaleString()+')':'');
    el.appendChild(lbl);

    const totalPages=Math.max(1,Math.ceil(files.length/state.PAGE));
    state.page=Math.min(state.page,totalPages-1);
    const slice=files.slice(state.page*state.PAGE,(state.page+1)*state.PAGE);

    const tbl=document.createElement('table');
    tbl.className='file-table';
    tbl.innerHTML='<thead><tr><th>Name</th><th>Size</th><th>Synced</th><th></th><th></th></tr></thead>';
    const tbody=document.createElement('tbody');
    for(const f of slice){{
      const tr=document.createElement('tr');
      const ext=getExt(f.n);
      const canPreview=IMG_EXT.has(ext)||VID_EXT.has(ext)||AUD_EXT.has(ext)||PDF_EXT.has(ext)||TXT_EXT.has(ext);
      const tdName=document.createElement('td');
      tdName.className='td-name'; tdName.title=f.n; tdName.textContent=f.n;
      const tdSize=document.createElement('td'); tdSize.textContent=hu(f.s);
      const tdDate=document.createElement('td'); tdDate.textContent=f.d;
      const tdView=document.createElement('td'); tdView.className='td-act';
      const tdDl  =document.createElement('td'); tdDl.className='td-act';
      // view button
      const viewBtn=document.createElement('button');
      viewBtn.className='act-btn';
      viewBtn.textContent=canPreview?'👁':'📄';
      viewBtn.title=canPreview?'Preview':'View (download)';
      viewBtn.addEventListener('click',()=>viewFile(f.k,f.n));
      tdView.appendChild(viewBtn);
      // download button
      const dlBtn=document.createElement('button');
      dlBtn.className='act-btn'; dlBtn.textContent='⬇'; dlBtn.title='Download';
      dlBtn.addEventListener('click',()=>downloadFile(f.k,f.n,dlBtn));
      tdDl.appendChild(dlBtn);
      tr.appendChild(tdName); tr.appendChild(tdSize);
      tr.appendChild(tdDate); tr.appendChild(tdView); tr.appendChild(tdDl);
      tbody.appendChild(tr);
    }}
    tbl.appendChild(tbody);
    el.appendChild(tbl);

    if(totalPages>1){{
      const pager=document.createElement('div'); pager.className='pager';
      const prev=document.createElement('button');
      prev.className='pg-btn'; prev.textContent='← Prev'; prev.disabled=state.page===0;
      prev.addEventListener('click',()=>{{state.page--;renderContent();}});
      const info=document.createElement('span');
      info.className='pg-info';
      info.textContent=`Page ${{state.page+1}} / ${{totalPages}}  (${{files.length.toLocaleString()}} files)`;
      const next=document.createElement('button');
      next.className='pg-btn'; next.textContent='Next →'; next.disabled=state.page>=totalPages-1;
      next.addEventListener('click',()=>{{state.page++;renderContent();}});
      pager.appendChild(prev); pager.appendChild(info); pager.appendChild(next);
      el.appendChild(pager);
    }}
  }} else if(!subdirs.length){{
    const e=document.createElement('div'); e.className='empty';
    e.textContent='This folder is empty.'; el.appendChild(e);
  }}
}}

// ── Events ────────────────────────────────────────────────────────────────────
document.getElementById('rail-search').addEventListener('input',e=>{{state.folderFilter=e.target.value;renderTree();}});
document.getElementById('search-bar').addEventListener('input',e=>{{state.search=e.target.value;state.page=0;renderContent();}});

// ── Resize handle ─────────────────────────────────────────────────────────────
const handle=document.getElementById('resize'), rail=document.getElementById('rail');
let drag=false,sx=0,sw=0;
handle.addEventListener('mousedown',e=>{{drag=true;sx=e.clientX;sw=rail.offsetWidth;handle.classList.add('drag');document.body.style.cssText='cursor:col-resize;user-select:none';e.preventDefault();}});
document.addEventListener('mousemove',e=>{{if(!drag)return;rail.style.width=Math.max(150,Math.min(600,sw+e.clientX-sx))+'px';}});
document.addEventListener('mouseup',()=>{{drag=false;handle.classList.remove('drag');document.body.style.cssText='';}});

// ── Init ──────────────────────────────────────────────────────────────────────
function render(){{renderTree();renderBreadcrumb();renderContent();}}
render();
</script>
</body>
</html>"""

    components.html(EXPLORER_HTML, height=720, scrolling=False)

# ── Sync Log ──────────────────────────────────────────────────────────────────

with tab_log:
    st.header("Sync History")
    log_entries = _parse_log()

    if not log_entries:
        if not LOG_FILE.exists():
            st.info("No backup.log found.")
        else:
            st.info("No completed sync runs found yet.")
    else:
        running = [e for e in log_entries if not e["done"]]
        done    = [e for e in log_entries if e["done"]]

        if running:
            st.subheader("🔄 Currently Running")
            for e in running:
                with st.container(border=True):
                    st.write(f"**{e['folder']}** — `{e['full_path']}`")
                    st.caption("Sync in progress…")

        if done:
            st.subheader("Completed Runs")
            for e in done:
                icon = "✅" if e["failed"] == 0 else "⚠️"
                with st.expander(f"{icon} {e['folder']} — {e['finished']}"):
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Uploaded",
                              f"{e['uploaded']:,} files" if isinstance(e["uploaded"], int) else e["uploaded"])
                    c2.metric("Size", e["size"])
                    c3.metric("Failed", e["failed"])
                    st.caption(f"Path: `{e['full_path']}`")
