# NashiGold RAG Chatbot

A bilingual (Arabic/English) jewellery assistant that answers from **your own products and policies** using RAG (retrieval-augmented generation) + Google Gemini (`gemini-3.5-flash-lite`).

## How it works

```
Customer question ──► embed question ──► search vector DB (ChromaDB)
                                               │ top matching products/policies
                                               ▼
                    Gemini (system prompt + retrieved context + chat history)
                                               │
Chat widget  ◄──────────── answer with product links ◄─┘
```

1. **Ingest** (`ingest.py`): each product row and each policy section becomes a "chunk", converted into an embedding vector with Gemini's multilingual `gemini-embedding-001` API (768-d, cosine) and stored in ChromaDB. No ML model runs locally, so the server fits in 512 MB RAM.
2. **Retrieve** (`rag.py`): the customer's question (plus their previous message, for follow-ups) is embedded and the closest chunks are fetched. Weak matches are dropped.
3. **Generate** (`app.py`): Gemini answers using only those chunks, in the customer's language, with product links. If nothing relevant is found, it offers WhatsApp support instead of guessing.

## Project layout

| File | Purpose |
|---|---|
| `data/products.csv` | Your catalog. Columns: `id, name_en, name_ar, category_en, category_ar, purity, weight_grams, description_en, description_ar` (`id`, `name_en`, `purity` required; optional `price_sar`, `stock`, `url` are used if present) |
| `data/*.md` | Policies & FAQs (shipping, returns, karats, sizing…); add as many files as you like |
| `ingest.py` | Builds the knowledge base, so re-run it after any data change |
| `rag.py` | Embeddings + vector search |
| `app.py` | FastAPI chat API + system prompt (brand voice and rules) |
| `static/widget.js` | Embeddable chat bubble (RTL-aware) |

## Run it locally

```bash
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # add your GEMINI_API_KEY (from Google AI Studio); loaded automatically
python ingest.py                # embeds all chunks via the Gemini API (a few minutes on the free tier)
uvicorn app:app --reload --port 8000
```

Open http://localhost:8000 and click the gold bubble.

## Deploy with Docker (e.g. Render free tier)

The image includes the pre-built `chroma_db/`, so run `python ingest.py` locally before building.

```bash
docker build -t nashigold-chat .
docker run -p 8000:8000 --env-file .env nashigold-chat
```

On Render, create a Docker web service and set `GEMINI_API_KEY`, `ALLOWED_ORIGINS` and `SUPPORT_WHATSAPP` as environment variables. Render sets `PORT` itself.

## Add it to your website

Deploy the backend (Render, Railway, a VPS, AWS/GCP…), set `ALLOWED_ORIGINS` to your domain, then paste one line before `</body>`:

```html
<script src="https://nashigold-chatbot.onrender.com/widget.js" data-api="https://nashigold-chatbot.onrender.com/"></script>
```

- **Shopify:** Online Store → Themes → Edit code → `theme.liquid`
- **WordPress/WooCommerce:** a plugin like "Insert Headers and Footers", or your theme footer
- **Custom site:** your main layout template

The server sends `widget.js` with `Cache-Control: no-cache`, so updates show up on the next page load. If your site or CDN caches scripts anyway, add a version and bump it after each update, e.g. `widget.js?v=2`.

## Keeping data fresh

- Export your catalog to `products.csv` (Shopify and WooCommerce both export CSV; map the columns) and re-run `python ingest.py`. Schedule it nightly with cron.
- Gold prices change daily, so the bot is instructed to call prices *indicative*. See "Live prices" below for exact pricing.

## Test before going live

Write 30–50 real customer questions (Arabic and English) and check the answers, e.g.:
- "عندكم أساور عيار 21 للأعراس؟"
- "Gift for my wife under 2,000 SAR"
- "Can I return a name necklace?"
- "What's the difference between 18K and 21K?"
- Something you *don't* sell (it should say so and offer WhatsApp, not invent a product)

## Next steps (recommended upgrades)

1. **Live prices & stock:** instead of indexing prices, give Gemini a *tool* (`get_product(id)`) that reads your store API at answer time.
2. **Filters:** for "under 3,000 SAR" or "21K only", add metadata filters (`where={"price_sar": {"$lte": 3000}}`) or let Gemini call a `search_products(max_price, karat, category)` tool.
3. **Hybrid search:** combine vector search with keyword search (BM25) so exact product names and SKUs always match.
4. **Streaming:** stream replies to the widget for a faster feel.
5. **Logging & analytics:** store conversations (with consent) to spot unanswered questions and add them to your FAQ files.
6. **Production hardening:** move rate limiting to Redis or your hosting provider, and add HTTPS and monitoring.
