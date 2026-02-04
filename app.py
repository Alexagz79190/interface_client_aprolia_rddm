import os
import stat
import streamlit as st
import paramiko
from io import StringIO

st.set_page_config(page_title="Explorateur SFTP", layout="wide")

if st.button("Reset connexion"):
    st.cache_resource.clear()
    st.rerun()

def get_env(name: str, default: str | None = None) -> str:
    v = os.getenv(name, default)
    if v is None or v == "":
        raise RuntimeError(f"Variable d'environnement manquante: {name}")
    return v

@st.cache_resource
def connect_sftp():
    host = st.secrets["SFTP_HOST"]
    port = int(st.secrets.get("SFTP_PORT", "22"))
    user = st.secrets["SFTP_USER"]
    key_text = st.secrets["SFTP_PRIVATE_KEY"]
    passphrase = st.secrets.get("SFTP_PASSPHRASE", None)

    from io import StringIO
    key_file = StringIO(key_text)

    pkey = None
    try:
        pkey = paramiko.Ed25519Key.from_private_key(key_file, password=passphrase)
    except Exception:
        key_file.seek(0)
        pkey = paramiko.RSAKey.from_private_key(key_file, password=passphrase)

    transport = paramiko.Transport((host, port))
    transport.connect(username=user, pkey=pkey)

    chan = transport.open_session()
    chan.invoke_subsystem("sftp")
    sftp = paramiko.SFTPClient.from_channel(chan)

    return transport, sftp

def is_dir(attr: paramiko.SFTPAttributes) -> bool:
    return stat.S_ISDIR(attr.st_mode)

def join_path(base: str, name: str) -> str:
    if base.endswith("/"):
        return base + name
    return base + "/" + name

st.title("Explorateur SFTP (lecture arborescence)")

try:
    transport, sftp = connect_sftp()
except Exception as e:
    st.error("Connexion SFTP impossible.")
    st.exception(e)
    st.stop()

# Chemin courant (session)
if "cwd" not in st.session_state:
    st.session_state.cwd = "/"

cwd = st.session_state.cwd

col1, col2 = st.columns([2, 1])
with col1:
    st.text_input("Dossier courant", value=cwd, key="cwd_input")
with col2:
    if st.button("Aller"):
        st.session_state.cwd = st.session_state.cwd_input

# Bouton parent
if st.button("⬅️ Remonter d'un niveau"):
    if cwd in [".", "/"]:
        st.session_state.cwd = "."
    else:
        parent = cwd.rsplit("/", 1)[0]
        st.session_state.cwd = parent if parent else "/"

cwd = st.session_state.cwd

st.write(f"**Contenu de :** `{cwd}`")

try:
    entries = sftp.listdir_attr(cwd)
except Exception as e:
    st.error(f"Impossible de lister `{cwd}`")
    st.exception(e)
    st.stop()

# Trier : dossiers d'abord puis fichiers
entries_sorted = sorted(entries, key=lambda a: (not is_dir(a), a.filename.lower()))

dirs = [e for e in entries_sorted if is_dir(e)]
files = [e for e in entries_sorted if not is_dir(e)]

st.subheader("Dossiers")
if not dirs:
    st.caption("Aucun dossier.")
else:
    for d in dirs:
        if st.button(f"📁 {d.filename}", key=f"dir_{cwd}_{d.filename}"):
            st.session_state.cwd = join_path(cwd, d.filename)

st.subheader("Fichiers")
if not files:
    st.caption("Aucun fichier.")
else:
    for f in files:
        size_kb = f.st_size / 1024
        st.write(f"📄 `{f.filename}` — {size_kb:.1f} KB")

