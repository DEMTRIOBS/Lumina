# database/cofre.py
"""Cifra de segredos com o DPAPI do Windows (escopo do usuario atual).

Usado para lembrar a senha de PDFs protegidos. A chave nao mora no app nem no
banco: e derivada da conta do Windows. Copiar o arquivo do banco para outra
maquina (ou outro usuario) entrega so lixo cifrado -- e o leitor simplesmente
volta a pedir a senha.

Fora do Windows `cifrar` devolve None: o chamador entao NAO grava nada, porque
guardar a senha em texto puro e pior do que pedir de novo.
"""
import base64
import sys

# Mistura propria do Lumina: outro programa do mesmo usuario que chame
# CryptUnprotectData sobre o blob sem esta "entropia" nao consegue abri-lo.
_ENTROPIA = b"Lumina/senha-de-livro/v1"
_SEM_INTERFACE = 0x1          # CRYPTPROTECT_UI_FORBIDDEN


def disponivel() -> bool:
    return sys.platform == "win32"


def _api():
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    assinatura = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
                  ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                  ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptProtectData.argtypes = assinatura
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, DATA_BLOB, crypt32, kernel32


def _blob(ctypes, DATA_BLOB, dados: bytes):
    # O buffer precisa sobreviver ate o fim da chamada: devolvido junto.
    buffer = ctypes.create_string_buffer(dados, len(dados))
    blob = DATA_BLOB(len(dados), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _chamar(funcao_nome: str, dados: bytes) -> bytes:
    ctypes, DATA_BLOB, crypt32, kernel32 = _api()
    entrada, _buf_entrada = _blob(ctypes, DATA_BLOB, dados)
    entropia, _buf_entropia = _blob(ctypes, DATA_BLOB, _ENTROPIA)
    saida = DATA_BLOB()
    funcao = getattr(crypt32, funcao_nome)
    if funcao_nome == "CryptProtectData":
        ok = funcao(ctypes.byref(entrada), "Lumina", ctypes.byref(entropia),
                    None, None, _SEM_INTERFACE, ctypes.byref(saida))
    else:
        ok = funcao(ctypes.byref(entrada), None, ctypes.byref(entropia),
                    None, None, _SEM_INTERFACE, ctypes.byref(saida))
    if not ok:
        raise OSError(ctypes.GetLastError(), f"{funcao_nome} falhou")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        # O DPAPI aloca a saida com LocalAlloc; sem liberar, cada senha
        # lembrada vazaria um pedaco de memoria.
        kernel32.LocalFree(ctypes.cast(saida.pbData, ctypes.c_void_p))


def cifrar(texto: str):
    """Texto -> base64 do blob DPAPI, ou None se nao der para cifrar."""
    if not disponivel() or texto is None:
        return None
    try:
        blob = _chamar("CryptProtectData", texto.encode("utf-8"))
        return base64.b64encode(blob).decode("ascii")
    except Exception as erro:
        print(f"[Cofre] Nao foi possivel cifrar: {erro}")
        return None


def decifrar(cifrado: str):
    """Inverso de `cifrar`; None se o blob for de outra conta ou estiver corrompido."""
    if not disponivel() or not cifrado:
        return None
    try:
        blob = base64.b64decode(cifrado.encode("ascii"))
        return _chamar("CryptUnprotectData", blob).decode("utf-8")
    except Exception:
        return None
