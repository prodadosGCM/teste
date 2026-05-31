import streamlit as st
import pandas as pd
from zoneinfo import ZoneInfo
from supabase import create_client
from datetime import datetime, date
import hashlib
import time
from io import BytesIO

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# =====================================================
# CONFIGURAÇÃO INICIAL DO STREAMLIT
# =====================================================
st.set_page_config(
    page_title="Escalas - GCMCF",
    layout="wide",
    initial_sidebar_state="expanded"
)

TZ = ZoneInfo("America/Sao_Paulo")

MESES = [
    "Janeiro", "Fevereiro", "Março", "Abril",
    "Maio", "Junho", "Julho", "Agosto",
    "Setembro", "Outubro", "Novembro", "Dezembro"
]

ANO_ATUAL = datetime.now(TZ).year
ANOS = [str(ano) for ano in range(ANO_ATUAL - 1, ANO_ATUAL + 3)]

ESCALAS_DISPONIVEIS = {
    "1º Distrito": "escala_1_distrito",
    "2º Distrito": "escala_2_distrito",
    "Marítima e Ambiental": "escala_maritima_ambiental"
}

# =====================================================
# FUNÇÕES DE SUPORTE E SEGURANÇA
# =====================================================
def make_hashes(password):
    return hashlib.sha256(str.encode(password.strip())).hexdigest()

def check_hashes(password, hashed_text):
    return make_hashes(password) == hashed_text.strip()

def gerar_nome_arquivo(prefixo_escala, nome_mes, ano):
    mes_limpo = nome_mes.lower().replace("ç", "c")
    return f"{prefixo_escala}_{mes_limpo}_{ano}.pdf"

def calcular_dias_24x72(mes_nome, ano_str, ala):
    """Lógica 24x72 baseada no padrão: D=1, A=2, B=3, C=4"""
    try:
        mes_idx = MESES.index(mes_nome) + 1
        ano_int = int(ano_str)
        offsets = {'D': 1, 'A': 2, 'B': 3, 'C': 4}
        dia_inicio = offsets[ala]
        dias = []
        for d in range(dia_inicio, 32, 4):
            try:
                # Valida se o dia existe no mês
                date(ano_int, mes_idx, d)
                dias.append(str(d))
            except: break
        return "-".join(dias)
    except: return ""

# =====================================================
# CONEXÃO E LOGS
# =====================================================
@st.cache_resource
def conectar_supabase():
    try:
        url = st.secrets["SUPABASE_URL"].strip()
        key = st.secrets["SUPABASE_KEY"].strip()
        return create_client(url, key)
    except: return None

supabase = conectar_supabase()

@st.cache_data(ttl=5)
def carregar_usuarios():
    if not supabase: return pd.DataFrame()
    res = supabase.table("usuarios").select("*").order("nome").execute()
    return pd.DataFrame(res.data)

def registrar_log(usuario, acao, detalhes=""):
    if not supabase: return
    agora = datetime.now(TZ)
    supabase.table("log_auditoria").insert({
        "data": agora.strftime("%d/%m/%Y"), "hora": agora.strftime("%H:%M:%S"),
        "usuario": str(usuario).upper(), "acao": str(acao).upper(), "detalhes": str(detalhes).upper()
    }).execute()

# =====================================================
# LÓGICA DE VALIDAÇÃO DE ESCALA
# =====================================================
def verificar_duplicidade_gcm_escala(escala_id, usuario_id):
    """Verifica se o GCM já está em qualquer ala da mesma escala mensal"""
    alas = supabase.table("escala_alas").select("id").eq("escala_id", escala_id).execute()
    ids_alas = [a['id'] for a in alas.data]
    if not ids_alas: return False
    res = supabase.table("escala_postos").select("id").in_("ala_id", ids_alas).eq("usuario_id", usuario_id).execute()
    return len(res.data) > 0

# =====================================================
# NOVAS VIEWS ADMINISTRATIVAS (ESTRUTURA DE ESCALA)
# =====================================================
def view_criar_escala_dinamica():
    st.markdown("### 🛠️ Administrar Estrutura de Escala (Alas e Postos)")
    
    tab_nova, tab_postos = st.tabs(["✨ Gerar Mês", "📋 Gerenciar Postos"])
    
    with tab_nova:
        with st.form("form_gerar_escala"):
            c1, c2, c3 = st.columns(3)
            dist = c1.selectbox("Unidade/Distrito", list(ESCALAS_DISPONIVEIS.keys()))
            m = c2.selectbox("Mês Referência", MESES)
            a = c3.selectbox("Ano", ANOS, index=1)
            
            if st.form_submit_button("Criar Estrutura 24x72"):
                data_ref = f"{a}-{MESES.index(m)+1:02d}-01"
                try:
                    res = supabase.table("escalas_config").insert({"mes_ano": data_ref, "tipo_distrito": dist}).execute()
                    esc_id = res.data[0]['id']
                    for letra in ['A', 'B', 'C', 'D']:
                        dias = calcular_dias_24x72(m, a, letra)
                        supabase.table("escala_alas").insert({"escala_id": esc_id, "letra_ala": letra, "dias_plantao": dias}).execute()
                    st.success("Estrutura criada com sucesso!")
                except: st.error("Erro: Esta escala já existe para este período.")

    with tab_postos:
        esc_list = supabase.table("escalas_config").select("*").order("mes_ano", desc=True).execute().data
        if not esc_list:
            st.info("Crie uma escala na aba ao lado primeiro.")
        else:
            opcoes = {f"{e['tipo_distrito']} | {e['mes_ano']}": e['id'] for e in esc_list}
            sel = st.selectbox("Selecione a Escala para Editar", list(opcoes.keys()))
            id_esc = opcoes[sel]
            
            alas_dados = supabase.table("escala_alas").select("*").eq("escala_id", id_esc).order("letra_ala").execute().data
            col_ala = st.radio("Selecione a Ala", ["A", "B", "C", "D"], horizontal=True)
            
            idx_ala = ["A", "B", "C", "D"].index(col_ala)
            ala_id = alas_dados[idx_ala]['id']
            st.info(f"Dias de Plantão Ala {col_ala}: {alas_dados[idx_ala]['dias_plantao']}")

            with st.expander("➕ Adicionar Novo Posto nesta Ala"):
                u_df = carregar_usuarios()
                nome_p = st.text_input("Posto (Ex: VTR 01, CCO, Supervisor)")
                cargo_p = st.selectbox("Função", ["CHEFE DE EQUIPE", "MOTORISTA", "COMPONENTE", "SUPERVISOR", "OPERADOR"])
                gcm_p = st.selectbox("GCM (Autocomplete)", ["-- Selecione --"] + u_df['nome'].tolist())
                
                if st.button("Salvar Alocação"):
                    if gcm_p != "-- Selecione --" and nome_p:
                        uid = int(u_df[u_df['nome'] == gcm_p].iloc[0]['id'])
                        if verificar_duplicidade_gcm_escala(id_esc, uid):
                            st.error(f"⚠️ O GCM {gcm_p} já está escalado em outra ala/posto este mês!")
                        else:
                            supabase.table("escala_postos").insert({
                                "ala_id": ala_id, "nome_posto": nome_p, "cargo_funcao": cargo_p, "usuario_id": uid
                            }).execute()
                            st.rerun()

            # Listagem de Postos Existentes
            st.markdown(f"**Postos Cadastrados - Ala {col_ala}**")
            postos = supabase.table("escala_postos").select("*, usuarios(nome)").eq("ala_id", ala_id).execute().data
            if postos:
                for p in postos:
                    col1, col2 = st.columns([4, 1])
                    col1.write(f"🔹 **{p['nome_posto']}**: {p['usuarios']['nome']} ({p['cargo_funcao']})")
                    if col2.button("🗑️", key=f"del_{p['id']}"):
                        supabase.table("escala_postos").delete().eq("id", p['id']).execute()
                        st.rerun()
            else:
                st.caption("Nenhum posto cadastrado para esta ala.")

# =====================================================
# CÓDIGO ORIGINAL ADAPTADO (LOGIN, SESSION, VIEWS)
# =====================================================

def login_usuario_supabase(tipo_usuario, login, senha):
    if not supabase: return {"sucesso": False, "erro": "Sem conexão"}
    try:
        res = supabase.table("usuarios").select("*").eq("login", str(login).strip()).execute()
        if not res.data: return {"sucesso": False, "erro": "Usuário não encontrado"}
        user = res.data[0]
        if str(user.get("status")).upper() != "ATIVO": return {"sucesso": False, "erro": "Inativo"}
        if str(user.get("tipo_usuario")).lower() != str(tipo_usuario).lower(): return {"sucesso": False, "erro": "Perfil incorreto"}
        if not check_hashes(senha, user["senha"]): return {"sucesso": False, "erro": "Senha incorreta"}
        return {"sucesso": True, "id": user["id"], "nome": user["nome"], "login": user["login"], "primeiro_acesso": user.get("primeiro_acesso") == 1}
    except Exception as e: return {"sucesso": False, "erro": str(e)}

# (Funções de PDF e Upload mantidas conforme o seu código)
def aplicar_marca_dagua(pdf_original_bytes, matricula):
    # ... (Sua lógica de marca d'água original aqui)
    return pdf_original_bytes 

def view_gerenciar_escala_admin():
    # ... (Seu código original de upload de PDF e gestão de usuários aqui)
    st.write("Módulo de Publicação de PDFs e Gestão de Contas")

def view_visualizar_escala_usuario():
    # ... (Seu código original de download de PDFs aqui)
    st.write("Área de consulta do GCM")

# --- FLUXO PRINCIPAL ---
def init_session():
    if "logado" not in st.session_state:
        st.session_state.update({"logado": False, "usuario_id": None, "tipo_usuario": None, "nome_usuario": "", "login_usuario": ""})

init_session()

if not st.session_state["logado"]:
    st.sidebar.title("🔐 Acesso Restrito")
    t = st.sidebar.selectbox("Função", ["agente", "admin"])
    l = st.sidebar.text_input("Matrícula")
    s = st.sidebar.text_input("Senha", type="password")
    if st.sidebar.button("Entrar"):
        res = login_usuario_supabase(t, l, s)
        if res["sucesso"]:
            st.session_state.update({"logado": True, "tipo_usuario": t, "nome_usuario": res["nome"], "login_usuario": res["login"], "usuario_id": res["id"]})
            st.rerun()
        else: st.sidebar.error("Dados inválidos")
else:
    st.sidebar.write(f"Usuário: **{st.session_state['nome_usuario']}**")
    
    if st.session_state["tipo_usuario"] == "admin":
        # MENU ADM COM A NOVA OPÇÃO
        menu = st.sidebar.radio("Navegação", ["Painel Admin (PDF/Contas)", "Estrutura de Escala (Novo)", "Relatório de Logs"])
        
        if menu == "Painel Admin (PDF/Contas)":
            # Aqui vai o seu código original 'view_gerenciar_escala_admin'
            tab1, tab2 = st.tabs(["Publicar PDF", "Gerenciar Usuários"])
            with tab1: st.info("Módulo original de PDF")
            with tab2: st.info("Módulo original de Usuários")
            
        elif menu == "Estrutura de Escala (Novo)":
            view_criar_escala_dinamica() # Função nova adicionada
            
        elif menu == "Relatório de Logs":
            st.subheader("Auditoria")
            # ... Seu código original de logs ...
    else:
        view_visualizar_escala_usuario()

    if st.sidebar.button("Sair"):
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()