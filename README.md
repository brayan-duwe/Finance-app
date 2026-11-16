# Finances💰

**Português** · [English](#english)

Um app de finanças pessoais feito com Django: importe a fatura do cartão, acompanhe para onde vai o seu dinheiro e converse com um assistente de IA que conhece os seus gastos.

## Funcionalidades

**Dashboard**
- Gráfico de rosca do mês: gastos por categoria, quanto sobrou da renda e os detalhes ao passar o mouse.
- Toque numa categoria para ver as despesas dela, da maior para a menor.
- Visão **Tendência**: gráfico de linhas de 3, 6 ou 12 meses, geral ou por categoria.
- Lista de transações com busca instantânea e filtro *Todas · Despesas · Receitas*.

**Importação de faturas (CSV ou PDF)**
- Detecta as colunas sozinho (data, descrição, valor, parcela, categoria do banco), em português, inglês ou espanhol.
- Com duas colunas de valor (por exemplo, US$ e R$), usa a da sua moeda e ignora a cotação.
- **Parcelas** (`3/4`) contam todo mês até a última, sem duplicar na fatura seguinte.
- Reconhece **assinaturas** mesmo quando o banco muda o código do nome ou o preço varia (até 20%), e oferece atualizar o preço sem mudar os meses anteriores.
- **Estornos** cancelam a cobrança correspondente. Pagamentos da fatura ficam de fora.
- Página de revisão em três grupos: *Despesas novas*, *Já cadastrados* e *Pagamentos e estornos*.

**Assistente de IA** (opcional, com a API do Claude)
- Perguntas como *"Onde estou gastando mais?"*, *"Posso comprar um celular de R$ 1.200 em 4 vezes?"* ou *"Compare meus últimos 3 meses"*.
- Adiciona, edita e exclui transações pelo chat, **sempre com a sua confirmação** num cartão antes de salvar.
- Responde no idioma escolhido, com valores no formato da sua moeda.

**Mais**
- Categorias personalizáveis (nome e cor), para despesas e receitas.
- Idiomas: português, inglês e espanhol. Moedas: R$, US$, €, £, ARS e MXN.
- Tema claro e escuro automático, animações suaves e suporte a teclado e leitores de tela.
- Funciona sem JavaScript, com as melhorias quando ele está disponível.

## Tecnologias

Python 3.14 · Django 6.1 · PostgreSQL ([Supabase](https://supabase.com)) ou SQLite · JavaScript sem frameworks · SVG para os gráficos · [Claude API](https://platform.claude.com/docs) (assistente) · pdfplumber (faturas em PDF)

## Como rodar

```bash
git clone <url-do-repositório>
cd Finances
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # depois preencha os valores (veja abaixo)
python manage.py migrate
python manage.py createsuperuser   # opcional: uma conta de administrador
python manage.py runserver
```

Abra http://127.0.0.1:8000, crie uma conta e adicione seu salário.

### Configuração (`.env`)

| Variável | Obrigatória | Para quê |
|---|---|---|
| `DJANGO_SECRET_KEY` | Sim | Chave secreta do Django. Gere com o comando no `.env.example`. |
| `DJANGO_DEBUG` | Não | `True` para desenvolvimento. Use `False` em produção. |
| `DATABASE_URL` | Não | Banco PostgreSQL, por exemplo do [Supabase](https://supabase.com) (use a string do *Session pooler*). Sem ela, o app usa um arquivo SQLite local. |
| `ANTHROPIC_API_KEY` | Não | Liga o assistente de IA. Sem ela, o app funciona normalmente, só sem o assistente. |
| `AI_MODEL` | Não | Modelo do Claude. Padrão: `claude-opus-5-5`. Para gastar menos: `claude-haiku-5-5`. |
| `AI_EFFORT` | Não | `low` (padrão, respostas rápidas), `medium` ou `high`. |

O `.env` tem segredos e **nunca** vai para o Git. O `.env.example` é só o modelo.

### Assistente de IA

1. Crie uma chave em [console.anthropic.com](https://console.anthropic.com) e adicione créditos.
2. Coloque `ANTHROPIC_API_KEY=...` no `.env` e reinicie o servidor.
3. Recomendado: defina um limite de gastos no Console.

O assistente envia à API da Anthropic apenas os dados que cada pergunta precisa. Ele nunca salva, edita ou exclui nada sem o seu clique.

## Testes

```bash
python manage.py test transactions
```

Os testes usam um Claude simulado, então não precisam de chave nem geram custo.

## Estrutura

```
config/                   configurações do Django
transactions/
  models.py               transações, categorias, parcelas e recorrências
  parsers.py              leitura das faturas (CSV/PDF)
  matching.py             reconhecimento de assinaturas
  refunds.py              estornos x cobranças
  assistant.py            assistente de IA (ferramentas e conversa)
  views.py                páginas e endpoints
  templates/, static/     HTML, CSS e JavaScript
locale/                   traduções (pt-BR, es)
```

---

<a id="english"></a>

# Finances

[Português](#finances) · **English**

A personal finance app built with Django: import your credit card statement, see where your money goes, and chat with an AI assistant that knows your spending.


## Features

**Dashboard**
- Monthly donut chart: spending per category, what's left of your income, and details on hover.
- Tap a category to see its expenses, biggest first.
- **Trend** view: a line chart for 3, 6 or 12 months, overall or per category.
- Transactions list with instant search and an *All · Expenses · Income* filter.

**Statement import (CSV or PDF)**
- Finds the columns by itself (date, description, amount, installment, bank category), in Portuguese, English or Spanish.
- With two amount columns (for example US$ and R$), it uses the one in your currency and ignores the exchange rate.
- **Installments** (`3/4`) count every month until the last one, without duplicates on the next statement.
- Recognises **subscriptions** even when the bank changes the code in the name or the price varies (up to 20%), and offers to update the price without changing past months.
- **Refunds** cancel the matching charge. Bill payments are left out.
- A review page in three groups: *New expenses*, *Already registered*, and *Payments and refunds*.

**AI assistant** (optional, using the Claude API)
- Questions like *"Where am I spending the most?"*, *"Can I afford a R$ 1,200 phone in 4 installments?"* or *"Compare my last 3 months"*.
- Adds, edits and deletes transactions by chat, **always confirmed by you** on a card before saving.
- Replies in your chosen language, with amounts in your currency format.

**More**
- Customisable categories (name and colour), for expenses and income.
- Languages: Portuguese, English and Spanish. Currencies: R$, US$, €, £, ARS and MXN.
- Automatic light and dark theme, smooth animations, and keyboard and screen reader support.
- Works without JavaScript, with enhancements when it's available.

## Tech stack

Python 3.14 · Django 6.1 · PostgreSQL ([Supabase](https://supabase.com)) or SQLite · framework-free JavaScript · SVG charts · [Claude API](https://platform.claude.com/docs) (assistant) · pdfplumber (PDF statements)

## Getting started

```bash
git clone <repository-url>
cd Finances
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # then fill in the values (see below)
python manage.py migrate
python manage.py createsuperuser   # optional: an admin account
python manage.py runserver
```

Open http://127.0.0.1:8000, create an account and add your salary.

### Configuration (`.env`)

| Variable | Required | What it's for |
|---|---|---|
| `DJANGO_SECRET_KEY` | Yes | Django's secret key. Generate one with the command in `.env.example`. |
| `DJANGO_DEBUG` | No | `True` for development. Use `False` in production. |
| `DATABASE_URL` | No | A PostgreSQL database, for example from [Supabase](https://supabase.com) (use the *Session pooler* string). Without it, the app uses a local SQLite file. |
| `ANTHROPIC_API_KEY` | No | Turns on the AI assistant. Without it, the app works normally, just without the assistant. |
| `AI_MODEL` | No | The Claude model. Default: `claude-opus-5-5`. To spend less: `claude-haiku-5-5`. |
| `AI_EFFORT` | No | `low` (default, quick answers), `medium` or `high`. |

`.env` holds secrets and is **never** committed to Git. `.env.example` is just the template.

### AI assistant

1. Create a key at [console.anthropic.com](https://console.anthropic.com) and add credit.
2. Put `ANTHROPIC_API_KEY=...` in `.env` and restart the server.
3. Recommended: set a spending limit in the Console.

The assistant sends Anthropic's API only the data each question needs. It never saves, edits or deletes anything without your click.

## Tests

```bash
python manage.py test transactions
```

The tests use a simulated Claude, so they need no key and cost nothing.

## Project structure

```
config/                   Django settings
transactions/
  models.py               transactions, categories, installments and recurring items
  parsers.py              statement reading (CSV/PDF)
  matching.py             subscription recognition
  refunds.py              refunds vs. charges
  assistant.py            AI assistant (tools and conversation)
  views.py                pages and endpoints
  templates/, static/     HTML, CSS and JavaScript
locale/                   translations (pt-BR, es)
```
