"""Administração das contas do My LLM Advisor pela linha de comando, no mesmo
banco do site (DB_* / DATABASE_URL de models/config.py). Serve para criar
contas sem código de convite, para redefinir a senha de quem a esqueceu (o
site não manda e-mail) e para passar a uma conta os PDFs da base sem dono.

  python manage_users.py list
  python manage_users.py add pessoa@exemplo.com              (pede a senha)
  python manage_users.py set-password pessoa@exemplo.com     (pede a nova; desconecta a conta)
  python manage_users.py delete pessoa@exemplo.com           (apaga a conta e as conversas)
  python manage_users.py adopt-documents pessoa@exemplo.com  (a base sem dono vira dela)

`adopt-documents` passa à conta a base `rag_documentos`: a do `rag_papers.py
index` de linha de comando, ou a base única de antes das contas. Precisa do
servidor do Chroma no ar (fala com ele por HTTP, como o site).

No fly.io: `fly ssh console --app myllmadvisor-gpu` e, lá dentro,
`cd /app && python manage_users.py ...`.
"""
import argparse
import getpass
import sys

from models import DEFAULT_BASE
from services import AuthService, IndexingService


def ask_password():
    password = getpass.getpass("Senha: ")
    if password != getpass.getpass("Repita a senha: "):
        sys.exit("As senhas não conferem.")
    return password


def main():
    parser = argparse.ArgumentParser(description="Contas do My LLM Advisor.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="lista as contas")
    for name, help_text in (("add", "cria uma conta"),
                            ("set-password", "define uma nova senha e desconecta a conta"),
                            ("delete", "apaga a conta e as conversas dela"),
                            ("adopt-documents", f"passa à conta os PDFs da base sem dono ({DEFAULT_BASE})")):
        sub.add_parser(name, help=help_text).add_argument("email")
    args = parser.parse_args()

    if args.command == "list":
        users = AuthService.list_users()
        for user in users:
            print(f"{user['email']:<40} criada em {user['created_at']:%d/%m/%Y}   {user['conversations']} conversa(s)")
        print(f"{len(users)} conta(s).")
    elif args.command == "add":
        user, error = AuthService.create_user(args.email, ask_password())
        if error:
            sys.exit(error)
        print(f"Conta criada: {user['email']}")
    elif args.command == "set-password":
        error = AuthService.set_password(args.email, ask_password())
        if error:
            sys.exit(error)
        print("Senha alterada; as sessões abertas da conta foram encerradas.")
    elif args.command == "delete":
        user = AuthService.find_user(args.email)
        if not user:
            sys.exit("Não existe conta com este e-mail.")
        if input(f"Apagar {user['email']}, as conversas e os PDFs dela? Digite 'sim': ").strip().lower() != "sim":
            sys.exit("Nada foi apagado.")
        AuthService.delete_user(user["email"])
        try:
            IndexingService.delete_user_documents(user["id"])
        except Exception as exc:
            sys.exit(f"Conta apagada, mas a base de documentos dela não (o Chroma está no ar?): {exc}")
        print("Conta apagada, com as conversas e os PDFs dela.")
    elif args.command == "adopt-documents":
        user = AuthService.find_user(args.email)
        if not user:
            sys.exit("Não existe conta com este e-mail.")
        try:
            pdfs, chunks = IndexingService.adopt_collection(user["id"])
        except Exception as exc:
            sys.exit(f"Não foi possível passar os documentos: {exc}")
        print(f"{pdfs} PDF(s) ({chunks} trechos) passaram para {user['email']}; a base {DEFAULT_BASE} foi apagada.")


if __name__ == "__main__":
    main()
