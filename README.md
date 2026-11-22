# aih_sus — Dashboard AIH SUS com assistente de IA 📊🤖

[![CI](https://github.com/caiorj2004/aih_sus/actions/workflows/ci.yml/badge.svg)](https://github.com/caiorj2004/aih_sus/actions/workflows/ci.yml)

Dashboard interativo em **Streamlit** sobre os procedimentos aprovados no **SIH/SUS** (DATASUS), com um **assistente de IA** que responde perguntas em português escrevendo e executando consultas SQL nos mesmos dados. Trabalho acadêmico do curso de Ciência de Dados e IA do IESB.

**App publicado:** https://cyurdu4dy3ksuvvs9rlwbj.streamlit.app/

---

## Em resumo

| Camada | O que faz | Tecnologia |
|---|---|---|
| Extração | Robô assíncrono que preenche o formulário TabNet (25 meses × quantidade e valor) e captura a tabela gerada | Playwright |
| Staging e produção | 50 tabelas de staging, limpeza (IGNORADO, código IBGE, separadores) e carga das matrizes `aih_qtd` e `aih_vl` com tipagem forte | SQLite → PostgreSQL (IESB) |
| Dados do app | Snapshot das tabelas de produção em Parquet, com municípios, UF e região do IBGE | `scripts/atualizar_dados.py` |
| Consulta | SQL direto sobre os Parquet, só com as colunas e linhas do recorte | DuckDB (somente leitura) |
| Dashboard | Filtros Ano → Mês → UF → Município, KPIs com delta, estatísticas descritivas, 6 gráficos, export CSV | Streamlit + Plotly |
| Assistente | Agente com *tool calling* que consulta os dados e mostra cada SQL executado | LangChain `create_agent` + Groq |

---

## O que mudou na versão 2 (e por quê)

A versão publicada no semestre tinha três problemas, resolvidos aqui:

1. **"End of Service" no Streamlit Cloud (estouro de memória).** O app trazia do PostgreSQL as matrizes largas (cerca de 110 colunas) várias vezes por tela, o cache guardava uma cópia nova a cada combinação de filtros (sem limite), a aba de lista convertia cada célula em texto e o assistente copiava os Parquets inteiros para um SQLite em memória. Agora:
   * o **DuckDB** lê o Parquet do disco e devolve só o recorte, já com UF;
   * o recorte é carregado **uma vez** e compartilhado pelas abas, com no máximo 2 recortes em cache;
   * a tabela mostra as primeiras 1.000 linhas, e o CSV completo é gerado **só quando o botão é clicado**.

   Medido com o `AppTest` do Streamlit: o recorte padrão (DF, 2026) usa cerca de 310 MB, e o pior caso (Brasil inteiro, todos os anos, 139 mil linhas) fica em cerca de 760 MB, abaixo do limite de 1 GB.
2. **Credenciais que expiravam.** O assistente usava credenciais temporárias da AWS (STS, com `AWS_SESSION_TOKEN`) e parava com `ExpiredTokenException` até alguém renovar a chave no console; isso aparece na tabela `agente_logs`. O provedor padrão agora é o **Groq**, com plano gratuito e chave que não expira. O AWS Bedrock continua disponível como opção, com usuário IAM de chave permanente.
3. **Agente frágil.** O agente ReAct por texto (`zero-shot-react-description`, até 15 iterações) foi trocado por um agente com *tool calling* (`langchain.agents.create_agent`), que chama uma ferramenta de SQL com argumentos estruturados.

Também saíram a dependência do banco para abrir o app e o "modo offline" sem UF: os Parquets agora trazem município, UF e região. O delta dos KPIs também foi corrigido: ele procurava o mês anterior com abreviações em inglês ("Dec") e ficava vazio.

---

## Assistente de IA

* **Como responde:** o modelo recebe o esquema real das tabelas, os períodos disponíveis e o dicionário dos 54 grupos de procedimento, e escreve SQL que a ferramenta `consultar_sql` executa no DuckDB.
* **Transparência:** cada resposta mostra as consultas SQL executadas, o número de linhas, o tempo e o modelo. Dá para conferir de onde veio cada número.
* **Segurança:**
  * o banco é aberto em **modo somente leitura**;
  * a ferramenta aceita uma única instrução `SELECT`/`WITH`;
  * funções que leem arquivos ou variáveis de ambiente (`read_csv`, `glob`, `getenv`...) são bloqueadas;
  * o resultado é limitado a 200 linhas.
* **Cota gratuita:** há um limite de perguntas por sessão (padrão 15), e perguntas repetidas são respondidas do cache, sem nova chamada ao modelo.
* **Logs:** pergunta e resposta vão para a tabela `agente_logs` do PostgreSQL, se configurado, ou para `logs/agente_logs.csv`. Servem de base para avaliar e melhorar o assistente.

---

## Executar localmente

```bash
pip install -r requirements.txt
streamlit run app.py
```

O dashboard funciona sem nenhuma configuração. Para o assistente, copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml` e preencha `GROQ_API_KEY` (gratuita em https://console.groq.com/keys).

Testes: `pip install pytest && pytest`. Os testes cobrem os totais conferidos com o PostgreSQL, a UF de todos os municípios, o banco somente leitura, a trava de SQL e o laço do agente com um modelo falso, que não gasta cota. A CI no GitHub Actions roda os testes e sobe o dashboard a cada push.

### Atualizar os dados

Quando o PostgreSQL receber meses novos:

```bash
python scripts/atualizar_dados.py --postgres "postgresql+psycopg2://usuario:senha@host:5432/banco"
```

O script regrava `data/` (matrizes em Parquet, municípios com UF e região, dicionário de procedimentos) e avisa se aparecer um município sem cadastro.

### Publicar no Streamlit Community Cloud

Aponte o app para `app.py` e cole o conteúdo do `secrets.toml` em *Settings > Secrets*.

---

## Funcionalidades do dashboard

| Aba | Conteúdo |
|-----|----------|
| 🏠 Introdução | Descrição do projeto e do pipeline |
| 📋 Lista dos Dados Armazenados | KPIs com delta, tabela do recorte, export CSV, dicionário de variáveis |
| 📈 Estatísticas Descritivas | Média, desvio-padrão, percentis, variância, assimetria e curtose por coluna |
| 📊 Gráficos Analíticos | Série temporal, ranking Top 10 (município ou UF), scatter com correlação de Pearson/Spearman, treemap por categoria e heatmap sazonal (mês × ano ou mês × UF) |
| 🤖 Assistente IA | Perguntas em linguagem natural, com as consultas SQL exibidas |

---

## Estrutura

```
aih_sus/
├── app.py                       # Interface Streamlit
├── dados.py                     # DuckDB (somente leitura) sobre os Parquet; consultas do dashboard
├── assistente.py                # Agente de IA: prompt com esquema, ferramenta SQL com trava, Groq/Bedrock
├── pagina_chat.py               # Aba do assistente: limites, cache, logs, exibição das consultas
├── scripts/atualizar_dados.py   # PostgreSQL (ou CSVs exportados) -> data/
├── data/
│   ├── aih_qtd.parquet          # quantidades aprovadas (município × mês × grupo de procedimento)
│   ├── aih_vl.parquet           # valores aprovados (R$)
│   ├── municipios.parquet       # municípios do IBGE com UF, região e coordenadas
│   └── dic_geral.csv            # grupos de procedimento (nome e código)
├── tests/                       # pytest
├── .github/workflows/ci.yml     # CI: testes + dashboard sobe sem erros
├── .streamlit/config.toml       # tema
├── .streamlit/secrets.toml.example
└── relatorio.tex                # relatório acadêmico do projeto
```

---

## Limitações conhecidas

* **Snapshot:** os dados vão de jan/2024 a jan/2026, exportados do PostgreSQL do IESB. Meses novos exigem rodar `scripts/atualizar_dados.py` e publicar de novo.
* **Código de extração:** o robô Playwright e o staging em SQLite descritos acima não estão neste repositório. Aqui ficam o app e o script que gera os dados do app a partir do banco de produção.
* **Cota gratuita do Groq:** com muito uso simultâneo, o assistente pode responder que a cota foi atingida. O dashboard não é afetado.
* **Qualidade das respostas:** perguntas vagas ("problemas do coração") dependem da escolha do grupo de procedimento pelo modelo. O prompt pede que ele diga quais grupos usou, e as consultas ficam visíveis para conferência.
* **Logs no Streamlit Cloud:** sem PostgreSQL configurado, o CSV de logs se perde quando o app reinicia.
