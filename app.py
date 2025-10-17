# -*- coding: utf-8 -*-
# FastAPI + pandas tabanlı: Excel → SEO Meta Üretici
# Notlar:
# - Yalnızca .xlsx kabul eder
# - Zorunlu sütunlar yoksa kullanıcıya anlamlı hata döner
# - Sadece ana ürünler (rootProductStockCode == 0) işlenir
# - Dolu title/description/keywords hücreleri asla üzerine yazılmaz
# - Sonuna title/description/keywords sütunlarını ekler (yoksa yaratır)
# - OPENAI_API_KEY varsa LLM (gpt-4o-mini) ile üretim yapılır; yoksa kural tabanlı
# - Render/Heroku vb. için Start: uvicorn app:app --host 0.0.0.0 --port $PORT

from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import pandas as pd
import io
import os
import re
from html import unescape
from typing import Optional

# -----------------------------------------------------------------------------
# UYGULAMA
# -----------------------------------------------------------------------------
app = FastAPI(title="Excel → SEO Meta Üretici")
templates = Jinja2Templates(directory="templates")

# LLM bayrağı: OPENAI_API_KEY varsa otomatik True olur
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
USE_LLM = bool(OPENAI_API_KEY)
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")  # hızlı/ekonomik iyi kalite

# HTML tag temizleyici
TAG_RE = re.compile(r"<[^>]+>")

# -----------------------------------------------------------------------------
# Yardımcı fonksiyonlar
# -----------------------------------------------------------------------------
def clean_html(raw_html: str) -> str:
    """Ürün detayı HTML içeriğini düz metne çevirir."""
    if not raw_html:
        return ""
    text = TAG_RE.sub("", str(raw_html))
    text = unescape(text)
    # Çoklu boşluk/kaçışları normalize et
    text = " ".join(text.split())
    return text.strip()

def trim_to_limit(text: str, max_chars: int) -> str:
    """Metni kelime bütünlüğünü koruyarak keser; üç nokta eklemez."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    cut_off_index = text[: max_chars + 1].rfind(" ")
    if cut_off_index == -1:
        cut_off_index = max_chars
    trimmed = text[:cut_off_index]
    # Sonda kalan eksik noktalama/boşlukları temizle
    trimmed = trimmed.rstrip(",.;:- ")
    return trimmed

# --- Yeni eklenen sabitler ve yardımcılar ---
TRIM_TITLE_MAX = 60
TRIM_DESC_MAX = 155

def pick_product_type(main_cat: str, cat: str, sub_cat: str) -> str:
    """En uygun ürün tipi adını seç (küçük harf, doğal ifade)."""
    for c in [sub_cat, cat, main_cat]:
        if c:
            txt = str(c).strip()
            if len(txt) >= 3:
                return txt.lower()
    return ""

def smart_join(*parts):
    """Boşları atıp tek boşlukla birleştirir."""
    return " ".join([str(p).strip() for p in parts if p and str(p).strip()])

def sentence_case(text: str) -> str:
    """Cümle başını büyütür, bağırmayı önler (Türkçe karakterleri korur)."""
    t = str(text or "").strip()
    if not t:
        return t
    return t[0].upper() + t[1:]

# -----------------------------------------------------------------------------
# Kural tabanlı üretim
# -----------------------------------------------------------------------------
def generate_title(brand: str, label: str, main_cat: str, cat: str, sub_cat: str) -> str:
    """
    SEO Title stratejisi:
    1) Zorunlu: ürün tipini açık et → (sub > cat > main)
    2) Şablon: [Marka] [Label] [ürün tipi]
    3) 50–60 karakter hedef; 45 altına düşerse label içi bir nitelik eklemeyi dene.
    """
    product_type = pick_product_type(main_cat, cat, sub_cat)
    base = smart_join(brand, label, product_type)
    title = trim_to_limit(base, TRIM_TITLE_MAX)

    # Çok kısa kaldıysa (ör. < 48), label içinden basit bir nitelik (128 gb, 750 ml vb.) yakala
    if len(title) < 48 and product_type:
        m = re.search(r"\b(\d{2,4}\s?(gb|ml|l|cm|mm|w))\b", str(label).lower())
        if m and m.group(0) not in title.lower():
            candidate = smart_join(brand, label, m.group(0), product_type)
            title = trim_to_limit(candidate, TRIM_TITLE_MAX)

    return title

def generate_description(clean_details: str, brand: str, label: str, category_name: str) -> str:
    """
    Description stratejisi:
    - Öncelik: details içindeki ilk anlamlı cümle (140–160 hedefine yakınsa onu kullan)
    - Yoksa: tek cümlelik tarafsız-doğal şablon üret.
    """
    # 1) Details'tan düzgün cümle yakala
    if clean_details:
        sentences = [s.strip() for s in re.split(r"[\.!\?]+", clean_details) if s.strip()]
        if sentences:
            cand = sentence_case(sentences[0])
            cand = trim_to_limit(cand, TRIM_DESC_MAX)
            if len(cand) >= 120:  # hedefe yakınsa bunu kullan
                return cand

    # 2) Şablon üret
    product_type = (category_name or "").lower().strip()
    base = smart_join(brand, label, product_type)
    if product_type:
        sent = f"{sentence_case(base)}. Günlük kullanıma uygun, net ve pratik bir seçimdir."
    else:
        sent = f"{sentence_case(smart_join(brand, label))} ile ihtiyacınızı karşılayan pratik bir çözümdür."
    return trim_to_limit(sent, TRIM_DESC_MAX)

def generate_keywords(brand: str, label: str, main_cat: str, cat: str, sub_cat: str, details_text: str) -> str:
    """
    2–3 anlamlı anahtar kelime:
    1) Marka + Label
    2) Ürün tipi (sub/category/main)
    3) Label içinden kapasite/numara gibi bir nitelik (varsa)
    """
    kws = []
    if brand and label:
        kws.append(f"{brand} {label}")
    else:
        if brand:
            kws.append(brand)
        if label:
            kws.append(label)

    product_type = pick_product_type(main_cat, cat, sub_cat)
    if product_type and product_type not in " ".join(kws).lower():
        kws.append(product_type)

    m = re.search(r"\b(\d{2,4}\s?(gb|ml|l|cm|mm|w))\b", str(label).lower())
    if m:
        val = m.group(0)
        if not any(val in k.lower() for k in kws):
            kws.append(val)

    return ", ".join(kws[:3])

# -----------------------------------------------------------------------------
# LLM katmanı (opsiyonel)
# -----------------------------------------------------------------------------
def llm_generate_meta(
    brand: str, label: str, main_cat: str, cat: str, sub_cat: str, clean_details: str
) -> Optional[dict]:
    """OpenAI'den title/description/keywords üretir. Hata/uygunsuzsa None döner."""
    if not USE_LLM or not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)

        category_name = (sub_cat or cat or main_cat or "").strip()

        system_msg = (
            "Sen deneyimli bir SEO editörüsün. Türkçe, akıcı, devriksiz yaz. "
            "Özel isimleri (brand, label) asla çevirme; tamamen büyük harf kullanma. "
            'ÇIKTIYI SADECE JSON döndür: {"title":"...","description":"...","keywords":["...","..."]}'
        )
        user_msg = f"""
Veriler (yalnız satırdan beslen):
- brand: {brand or ""}
- label: {label or ""}
- main/category/sub: {main_cat or ""} / {cat or ""} / {sub_cat or ""}
- details (HTML temiz): {clean_details or ""}

Kurallar:
- Title: 50–60 karakter hedef; ürün tipini açık et (örn. akıllı telefon / koşu ayakkabısı / şampuan).
  Şablon esnek: [Marka] [Label] [ürün tipi veya anlamlı nitelik].
  Kelime ortasında kesme yok, üç nokta yok.
- Description: 140–160 karakter, tek cümle (en fazla iki kısa cümle). Doğal ve tarafsız.
- Keywords: 2–3 anlamlı anahtar (örn. 'Apple iPhone 14 Pro Max', 'akıllı telefon', '128 gb').
- Alan dışı varsayım yapma; ürün tipini kategori adlarından türet.
"""
        resp = client.responses.create(
            model=LLM_MODEL,
            input=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.4,
        )
        content = resp.output_text

        import json
        data = json.loads(content)

        title = trim_to_limit(str(data.get("title", "")).strip(), TRIM_TITLE_MAX)
        desc = trim_to_limit(str(data.get("description", "")).strip(), TRIM_DESC_MAX)
        kws = data.get("keywords", [])
        if isinstance(kws, list):
            keywords = ", ".join([str(x).strip() for x in kws if str(x).strip()])[:200]
        else:
            keywords = str(kws).strip()

        # Basit kalite kontrol: çok kısa ise fallback'e izin ver
        if len(title) < 45 or len(desc) < 120 or not keywords:
            return None

        return {"title": title, "description": desc, "keywords": keywords}
    except Exception:
        # LLM'de hata olursa sessizce kural tabanlıya düşeceğiz
        return None

# -----------------------------------------------------------------------------
# Rotalar
# -----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Form sayfasını döndürür."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/health", response_class=HTMLResponse)
async def health():
    return HTMLResponse("ok")

@app.post("/upload", response_class=HTMLResponse)
async def upload_file(request: Request, file: UploadFile = File(...)):
    """
    Excel dosyasını alır, işler ve sonucu indirmeye hazırlar.
    Hata durumunda sayfayı anlamlı mesajla döndürür (500 yerine).
    """
    # 1) Dosya tipi doğrulama
    try:
        filename = file.filename or ""
        if not filename.lower().endswith(".xlsx"):
            return templates.TemplateResponse(
                "index.html",
                {"request": request, "error": "Lütfen .xlsx formatında bir Excel dosyası yükleyin."},
            )
    except Exception:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": "Dosya okunamadı. Tekrar deneyin."},
        )

    # 2) Excel'i oku
    try:
        content = await file.read()
        excel_data = io.BytesIO(content)
        df = pd.read_excel(excel_data, engine="openpyxl")
    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": f"Dosya okunamadı: {e}"},
        )

    # 3) Gerekli sütunlar
    required_columns = ["label", "brand", "mainCategory", "rootProductStockCode"]
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": "Eksik sütun(lar): " + ", ".join(missing)},
        )

    # opsiyonel details sütunu isim varyantları
    details_col = None
    for col in ["details", "detail", "description", "aciklama", "urun_detayi", "urunDetayi"]:
        if col in df.columns:
            details_col = col
            break

    # 4) Meta sütunlarını hazırla
    for col in ["title", "description", "keywords"]:
        if col not in df.columns:
            df[col] = ""

    # 5) Satırları işle
    for idx, row in df.iterrows():
        # Sadece ana ürünler
        try:
            if pd.isna(row["rootProductStockCode"]) or row["rootProductStockCode"] != 0:
                continue
        except Exception:
            continue

        # Dolu hücre varsa dokunma
        current_title = str(row["title"]) if "title" in df.columns and not pd.isna(row["title"]) else ""
        current_desc = str(row["description"]) if "description" in df.columns and not pd.isna(row["description"]) else ""
        current_keywords = str(row["keywords"]) if "keywords" in df.columns and not pd.isna(row["keywords"]) else ""
        if current_title or current_desc or current_keywords:
            continue

        # Alanları topla
        brand = str(row["brand"]) if not pd.isna(row["brand"]) else ""
        label = str(row["label"]) if not pd.isna(row["label"]) else ""
        main_cat = str(row["mainCategory"]) if not pd.isna(row["mainCategory"]) else ""
        cat = str(row["category"]) if "category" in df.columns and not pd.isna(row["category"]) else ""
        sub_cat = str(row["subCategory"]) if "subCategory" in df.columns and not pd.isna(row["subCategory"]) else ""

        details_text = ""
        if details_col:
            raw_details = str(row[details_col]) if not pd.isna(row[details_col]) else ""
            details_text = clean_html(raw_details)

        # 1) LLM dene
        new_title = ""
        new_desc = ""
        new_keywords = ""
        llm_out = llm_generate_meta(brand, label, main_cat, cat, sub_cat, details_text)
        if llm_out:
            new_title = llm_out["title"]
            new_desc = llm_out["description"]
            new_keywords = llm_out["keywords"]

        # 2) LLM başarısızsa kural tabanlı üret
        if not new_title or not new_desc or not new_keywords:
            new_title = generate_title(brand, label, main_cat, cat, sub_cat)
            category_name = sub_cat or cat or main_cat
            new_desc = generate_description(details_text, brand, label, category_name)
            new_keywords = generate_keywords(brand, label, main_cat, cat, sub_cat, details_text)

        # DataFrame'e yaz
        df.at[idx, "title"] = new_title
        df.at[idx, "description"] = new_desc
        df.at[idx, "keywords"] = new_keywords

    # 6) Excel'i bellekten döndür
    try:
        output_buffer = io.BytesIO()
        with pd.ExcelWriter(output_buffer, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        output_buffer.seek(0)
        out_name = filename[:-5] + "_seo.xlsx" if filename.lower().endswith(".xlsx") else filename + "_seo.xlsx"
        return StreamingResponse(
            output_buffer,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={out_name}"},
        )
    except Exception as e:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": f"Çıktı oluşturulamadı: {e}"},
        )
