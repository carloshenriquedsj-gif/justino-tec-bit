# Justino Tec Bit v1.1 — teste online

Sistema web de notas fiscais, produtos e relatórios com **tela de login e sessão assinada**. O logotipo enviado foi mantido.

## Publicar gratuitamente no Render

1. Crie uma conta no GitHub e um repositório **privado** novo (por exemplo, `justino-tec-bit`).
2. Extraia o ZIP e envie **o conteúdo da pasta** `justino_tec_bit_online` ao repositório. Não envie senhas, notas reais ou banco `.db`.
3. Crie uma conta no https://render.com e escolha **New > Blueprint**; conecte o repositório do GitHub.
4. O Render lerá `render.yaml`. Selecione o plano gratuito, se disponível. Na variável `ADMIN_PASSWORD`, informe uma senha forte e exclusiva. `SESSION_SECRET` é gerado automaticamente. Não use a senha da Shopee, do Gmail ou de outro serviço.
5. Confirme a criação e aguarde o deploy. Na página do serviço, copie a URL `https://...onrender.com` fornecida pelo Render. **A URL exata só existe depois da publicação na sua conta.**
6. Abra a URL e faça login com usuário `admin` e a senha que você configurou.

## Rodar localmente

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:ADMIN_USER='admin'
$env:ADMIN_PASSWORD='troque-por-uma-senha-forte'
$env:SESSION_SECRET='troque-por-um-segredo-aleatorio-longo'
$env:COOKIE_SECURE='false'
uvicorn app:app --reload
```

Abra http://127.0.0.1:8000. Nunca use `COOKIE_SECURE=false` em produção.

## Avisos essenciais

- **Render gratuito usa disco efêmero**: o banco SQLite local **pode ser apagado a cada reinício ou novo deploy**. Use somente dados fictícios nos testes. Para guardar notas e dados reais, será preciso migrar para banco persistente (ex.: PostgreSQL), com backups, permissões e revisão de segurança/LGPD.
- A tela de login protege a interface e endpoints; não há ainda múltiplos usuários, recuperação de senha, MFA, auditoria ou proteção avançada contra ataques. O limitador de tentativas é simples, em memória.
- O XML importa itens de NF-e. PDFs com texto são registrados para revisão, mas os itens **não** são extraídos automaticamente.
- A identificação do marketplace é manual. **Não há integração real** com APIs da Shopee, Mercado Livre, Nuvemshop ou Amazon nesta versão.
- Importação não prova pagamento ou entrega; relatórios são baseados em itens de NF-e, não em pedidos liquidados.
- O acesso público exige uma conta Render/GitHub sua; este ZIP **não** cria sozinho um endereço ativo.
