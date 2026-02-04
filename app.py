import stat
import posixpath
from io import StringIO
import streamlit as st
import paramiko
import hashlib
import hmac

st.set_page_config(page_title="SFTP Orders / Archive / Status", layout="wide")

# --- Répertoires (relatifs au "home" SFTP) ---
ORDERS_DIR = "./orders"
ARCHIVE_DIR = "./archive"
STATUS_DIR = "./status"

# ----------------- AUTH -----------------
def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def check_login(username: str, password: str) -> bool:
    users = st.secrets.get("AUTH_USERS", [])
    pwd_hash = _sha256_hex(password)

    for u in users:
        if u.get("username") == username:
            stored = u.get("password_sha256", "")
            return hmac.compare_digest(pwd_hash, stored)
    return False

def require_auth():
    if st.session_state.get("authenticated"):
        return

    st.title("Connexion")
    with st.form("login_form"):
        username = st.text_input("Utilisateur")
        password = st.text_input("Mot de passe", type="password")
        submitted = st.form_submit_button("Se connecter")

    if submitted:
        if check_login(username.strip(), password):
            st.session_state.authenticated = True
            st.session_state.username = username.strip()
            st.success("Connexion OK")
            st.rerun()
        else:
            st.error("Identifiants incorrects")

    st.stop()

# On exige la connexion AVANT d'afficher le reste
require_auth()

with st.sidebar:
    st.write(f"Connecté : **{st.session_state.get('username','')}**")
    if st.button("Déconnexion"):
        st.session_state.authenticated = False
        st.session_state.username = ""
        st.rerun()

# ----------------- SFTP CONNECT -----------------
@st.cache_resource
def connect_sftp():
    host = st.secrets["SFTP_HOST"]
    port = int(st.secrets.get("SFTP_PORT", "22"))
    user = st.secrets["SFTP_USER"]
    key_text = st.secrets["SFTP_PRIVATE_KEY"]
    passphrase = st.secrets.get("SFTP_PASSPHRASE", None)

    key_file = StringIO(key_text)

    try:
        pkey = paramiko.Ed25519Key.from_private_key(key_file, password=passphrase)
    except Exception:
        key_file.seek(0)
        pkey = paramiko.RSAKey.from_private_key(key_file, password=passphrase)

    transport = paramiko.Transport((host, port))
    transport.connect(username=user, pkey=pkey)
    sftp = paramiko.SFTPClient.from_transport(transport)

    # Stabilisation
    try:
        sftp.chdir("/")
    except Exception:
        pass

    return transport, sftp


def is_dir(attr):
    return stat.S_ISDIR(attr.st_mode)


def ensure_dir(sftp: paramiko.SFTPClient, path: str) -> bool:
    """Crée le dossier si absent. Retourne True si OK/existe."""
    try:
        sftp.stat(path)
        return True
    except Exception:
        try:
            sftp.mkdir(path)
            return True
        except Exception:
            return False


def list_files(sftp: paramiko.SFTPClient, folder: str):
    """Liste uniquement les fichiers (pas les dossiers) d'un répertoire."""
    entries = sftp.listdir_attr(folder)
    files = [e for e in entries if not is_dir(e)]
    files.sort(key=lambda e: e.filename.lower())
    return files


def read_remote_file(sftp: paramiko.SFTPClient, remote_path: str) -> bytes:
    with sftp.open(remote_path, "rb") as f:
        return f.read()


def upload_fileobj(sftp: paramiko.SFTPClient, remote_path: str, uploaded_file) -> None:
    # uploaded_file est un st.uploaded_file (BytesIO-like)
    with sftp.open(remote_path, "wb") as f:
        f.write(uploaded_file.getbuffer())


def archive_file(sftp: paramiko.SFTPClient, src_path: str, archive_dir: str) -> str:
    """Déplace src_path vers archive_dir. Renvoie le chemin destination."""
    filename = posixpath.basename(src_path)
    dst_path = posixpath.join(archive_dir, filename)

    # Si un fichier existe déjà en archive, on suffixe
    try:
        sftp.stat(dst_path)
        base, ext = posixpath.splitext(filename)
        i = 1
        while True:
            candidate = posixpath.join(archive_dir, f"{base}__{i}{ext}")
            try:
                sftp.stat(candidate)
                i += 1
            except Exception:
                dst_path = candidate
                break
    except Exception:
        pass

    sftp.rename(src_path, dst_path)
    return dst_path


# ----------------- UI -----------------
st.title("Interface SFTP : AIR DDM")

colA, colB = st.columns([1, 1])
with colA:
    if st.button("Reset connexion"):
        st.cache_resource.clear()
        st.rerun()

with colB:
    refresh = st.button("Rafraîchir listes")

try:
    transport, sftp = connect_sftp()
except Exception as e:
    st.error("Connexion SFTP impossible (secrets / clé / passphrase).")
    st.exception(e)
    st.stop()

# Vérif / création dossiers
ok_orders = ensure_dir(sftp, ORDERS_DIR)
ok_archive = ensure_dir(sftp, ARCHIVE_DIR)
ok_status = ensure_dir(sftp, STATUS_DIR)

if not ok_orders:
    st.warning(f"Impossible d'accéder/créer `{ORDERS_DIR}` (droits SFTP ?).")
if not ok_archive:
    st.warning(f"Impossible d'accéder/créer `{ARCHIVE_DIR}` (droits SFTP ?).")
if not ok_status:
    st.warning(f"Impossible d'accéder/créer `{STATUS_DIR}` (droits SFTP ?).")

tab1, tab2 = st.tabs(["📥 Orders (télécharger + archiver)", "📤 Status (uploader)"])

with tab1:
    st.subheader(f"Fichiers dans {ORDERS_DIR}")

    if not ok_orders:
        st.stop()

    try:
        files = list_files(sftp, ORDERS_DIR)
    except Exception as e:
        st.error(f"Impossible de lister `{ORDERS_DIR}`")
        st.exception(e)
        st.stop()

    if not files:
        st.info("Aucun fichier dans orders.")
    else:
        for fattr in files:
            fname = fattr.filename
            remote_path = posixpath.join(ORDERS_DIR, fname)

            c1, c2, c3, c4 = st.columns([4, 2, 2, 2])
            with c1:
                st.write(f"📄 `{fname}`")
            with c2:
                st.write(f"{fattr.st_size/1024:.1f} KB")
            with c3:
                # Download button : on lit le fichier au moment de l'affichage
                try:
                    data = read_remote_file(sftp, remote_path)
                    st.download_button(
                        label="Télécharger",
                        data=data,
                        file_name=fname,
                        mime="application/octet-stream",
                        key=f"dl_{remote_path}",
                    )
                except Exception as e:
                    st.button("Télécharger", disabled=True, key=f"dl_disabled_{remote_path}")
            with c4:
                if st.button("Archiver", key=f"arch_{remote_path}", disabled=not ok_archive):
                    try:
                        dst = archive_file(sftp, remote_path, ARCHIVE_DIR)
                        st.success(f"Archivé vers `{dst}`")
                        st.rerun()
                    except Exception as e:
                        st.error("Échec archivage.")
                        st.exception(e)

with tab2:
    st.subheader(f"Déposer un fichier dans {STATUS_DIR}")

    if not ok_status:
        st.stop()

    uploaded = st.file_uploader(
        "Choisir un fichier Excel",
        type=["xlsx", "xls"],
        accept_multiple_files=False
    )

    rename = st.text_input("Nom de fichier cible (optionnel)", value="")

    if uploaded is not None:
        target_name = rename.strip() if rename.strip() else uploaded.name
        remote_target = posixpath.join(STATUS_DIR, target_name)

        if st.button("Uploader vers status"):
            try:
                upload_fileobj(sftp, remote_target, uploaded)
                st.success(f"Upload OK → `{remote_target}`")
            except Exception as e:
                st.error("Upload KO.")
                st.exception(e)
