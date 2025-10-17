from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import pandas as pd
import io, re, os 
from html import unescape

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# LLM kullanım bayrağı (varsayılan kapalı, ileride OPENAI_API_KEY varsa kullanılabilir)
USE_LLM = False
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# HTML tag'leri temizlemek için regex deseni (ör. <br>, <p> vb. hepsini kaldırır)
TAG_RE = re.compile(r'<[^>]+>')

def clean_html(raw_html: str) -> str:
    """Ürün detayı HTML içeriğini düz metne çevirir."""
    if not raw_html:
        return ""
    # HTML taglerini kaldır
    text = TAG_RE.sub('', raw_html)  # HTML etiketlerini siler:contentReference[oaicite:11]{index=11}
    # HTML entity'lerini dönüştür (örn &amp; -> & , &nbsp; -> boşluk)
    text = unescape(text)
    # Beyazlukları normalleştir: fazla boşlukları tek boşluk yap, baş/son boşlukları kırp
    text = ' '.join(text.split())
    return text.strip()

def trim_to_limit(text: str, max_chars: int) -> str:
    """Metni max_chars sınırında kelime bütünlüğünü koruyarak keser."""
    if len(text) <= max_chars:
        return text
    # max_chars sınırını aşmışsa, max_chars+1 uzunluğuna kadar alıp son boşluğun konumunu bul
    cut_off_index = text[:max_chars+1].rfind(" ")
    if cut_off_index == -1:
        # Hiç boşluk bulunamadıysa direkt max_chars sınırında kes
        cut_off_index = max_chars
    trimmed = text[:cut_off_index]
    # Sondaki eksik noktalama işaretlerini temizle (virgül, nokta vb.)
    trimmed = trimmed.rstrip(",.;:- ")
    return trimmed

# --- Yeni eklenen sabitler ve yardımcılar ---
TRIM_TITLE_MAX = 60
TRIM_DESC_MAX  = 155

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
    return " ".join([p.strip() for p in parts if p and str(p).strip()])

def sentence_case(text: str) -> str:
    """Cümle başını büyütür, bağırmayı önler (Türkçe karakterleri korur)."""
    t = str(text).strip()
    if not t:
        return t
    return t[0].upper() + t[1:]


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
        if brand: kws.append(brand)
        if label: kws.append(label)

    product_type = pick_product_type(main_cat, cat, sub_cat)
    if product_type and product_type not in " ".join(kws).lower():
        kws.append(product_type)

    m = re.search(r"\b(\d{2,4}\s?(gb|ml|l|cm|mm|w))\b", str(label).lower())
    if m:
        val = m.group(0)
        if not any(val in k.lower() for k in kws):
            kws.append(val)

    return ", ".join(kws[:3])


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Form sayfasını döndürür."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload", response_class=HTMLResponse)
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Excel dosyasını alır, işler ve sonucu indirmeye hazırlar."""
    # 1. Dosya tipi doğrulama
    filename = file.filename
    if not filename.lower().endswith(".xlsx"):
        error_msg = "Lütfen .xlsx formatında bir Excel dosyası yükleyin."
        return templates.TemplateResponse("index.html", {"request": request, "error": error_msg})
    # 2. Excel'i oku
    try:
        # Dosyayı bellek içine okuyup pandas ile DataFrame'e çeviriyoruz
        content = await file.read()
        excel_data = io.BytesIO(content)
        df = pd.read_excel(excel_data, engine="openpyxl")
    except Exception as e:
        error_msg = f"Dosya okunamadı: {e}"
        return templates.TemplateResponse("index.html", {"request": request, "error": error_msg})
    # 3. Gerekli sütunların kontrolü
    required_columns = ["label", "brand", "mainCategory", "rootProductStockCode"]
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        error_msg = "Eksik sütunlar: " + ", ".join(missing)
        return templates.TemplateResponse("index.html", {"request": request, "error": error_msg})
    # Opsiyonel sütunlardan mevcut olanları al
    details_col = None
    for col in ["details", "detail", "description", "aciklama"]:  # farklı isim varyasyonları kontrol
        if col in df.columns:
            details_col = col
            break
    # 4. Mevcut meta sütunları kontrol et, yoksa ekle
    meta_cols = ["title", "description", "keywords"]
    for col in meta_cols:
        if col not in df.columns:
            df[col] = ""  # yeni sütun ekle
    # 5. Satırları işle ve meta alanları doldur
    for idx, row in df.iterrows():
        try:
            # Sadece ana ürün (rootProductStockCode == 0) satırları işlenecek
            if pd.isna(row["rootProductStockCode"]) or row["rootProductStockCode"] != 0:
                continue  # varyant veya geçersiz değer, meta üretmiyoruz
        except KeyError:
            # rootProductStockCode yoksa (beklenmedik durum), o satırı atla
            continue
        # Title, Description, Keywords zaten dolu ise atla (değiştirme)
        current_title = str(row["title"]) if not pd.isna(row["title"]) else ""
        current_desc = str(row["description"]) if not pd.isna(row["description"]) else ""
        current_keywords = str(row["keywords"]) if not pd.isna(row["keywords"]) else ""
        if current_title or current_desc or current_keywords:
            # Bu ana ürün satırında herhangi bir meta alan önceden doldurulmuşsa, dokunmuyoruz
            continue
        # Gerekli temel alanları al
        brand = str(row["brand"]) if not pd.isna(row["brand"]) else ""
        label = str(row["label"]) if not pd.isna(row["label"]) else ""
        main_cat = str(row["mainCategory"]) if not pd.isna(row["mainCategory"]) else ""
        cat = str(row["category"]) if "category" in row and not pd.isna(row["category"]) else ""
        sub_cat = str(row["subCategory"]) if "subCategory" in row and not pd.isna(row["subCategory"]) else ""
        # Ürün detayını temizle (HTML'den arındır)
        details_text = ""
        if details_col:
            raw_details = str(row[details_col]) if not pd.isna(row[details_col]) else ""
            details_text = clean_html(raw_details)
        # Meta alanları üret
        new_title = generate_title(brand, label, main_cat, cat, sub_cat)
        # Title içerisinde anahtar kategori kelimesi varsa, description için kategori adını o olarak kullanabiliriz
        category_name = sub_cat or cat or main_cat
        new_desc = generate_description(details_text, brand, label, category_name)
        new_keywords = generate_keywords(brand, label, main_cat, cat, sub_cat, details_text)
        # DataFrame'e yaz
        df.at[idx, "title"] = new_title
        df.at[idx, "description"] = new_desc
        df.at[idx, "keywords"] = new_keywords
    # 6. DataFrame'i Excel olarak çıktıya hazırla
    # Belleğe yaz
    output_buffer = io.BytesIO()
    with pd.ExcelWriter(output_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    output_buffer.seek(0)
    # İndirme için dosya adı (orijinal isme _seo eki eklenebilir)
    out_name = filename
    if out_name.lower().endswith(".xlsx"):
        out_name = out_name[:-5] + "_seo.xlsx"
    else:
        out_name = out_name + "_seo.xlsx"
    # 7. Excel dosyasını yanıt olarak döndür (indirme başlat)
    return StreamingResponse(output_buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                              headers={"Content-Disposition": f"attachment; filename={out_name}"})
