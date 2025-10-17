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

def generate_title(brand: str, label: str, main_cat: str, cat: str, sub_cat: str) -> str:
    """Ürün için SEO title üretir."""
    # LLM ile zenginleştirme opsiyonu (gelecek için; şu an kapalı)
    if USE_LLM and OPENAI_API_KEY:
        # Örn: burada bir harici API çağrısı ile title üretilebilir.
        pass

    parts = []
    if brand:
        parts.append(brand)
    # label genelde zaten ürün/model adıdır
    if label:
        # Eğer label, brand bilgisini içermiyorsa eklenir (çoğu durumda brand ayrı sütunda veriliyor)
        # Bu araçta brand ayrı verildiği için label direkt kullanıyoruz.
        parts.append(label)
    # En spesifik kategori (subCategory varsa onu, yoksa category, o da yoksa mainCategory)
    category_name = sub_cat or cat or main_cat
    if category_name:
        # Kategori bilgisini küçük harfle ekle (doğal bir tanım olması için)
        category_phrase = category_name.lower()
        # Eğer brand+label birleşiminde kategori zaten geçiyorsa tekrarlamaya gerek yok
        title_text = " ".join(parts)
        if category_phrase not in title_text.lower():
            parts.append(category_phrase)
    title = " ".join(parts)
    # Karakter sınırına göre kes
    title = trim_to_limit(title, 60)
    return title

def generate_description(clean_details: str, brand: str, label: str, category_name: str) -> str:
    """Ürün için SEO açıklama (description) üretir."""
    # LLM ile zenginleştirme opsiyonu (gelecek için; şu an kapalı)
    if USE_LLM and OPENAI_API_KEY:
        # Örn: burada bir harici API çağrısı ile açıklama üretilebilir.
        pass

    desc = ""
    # Eğer ürün detayı (açıklama) mevcutsa ilk cümleyi almaya çalış
    if clean_details:
        # Noktaya göre cümleleri ayır
        sentences = [s.strip() for s in clean_details.split('.') if s.strip()]
        if sentences:
            # İlk cümleyi al
            desc = sentences[0]
            # Eğer ilk cümle çok kısaysa (140 karakterden az) ve ikinci cümle varsa, onu da ekle
            if len(desc) < 140 and len(sentences) > 1:
                # İki cümleyi birleştirirken araya nokta ekleyip toplamı kontrol edeceğiz
                combined = desc + ". " + sentences[1]
                if len(combined) <= 160:
                    desc = combined
                else:
                    # İkinci cümleyi eklerken 160'ı aşıyorsa, ilk cümleye yakın bir uzunlukta tutarız
                    desc = trim_to_limit(combined, 160)
    # Eğer detayı yoksa ya da uygun cümle bulunamadıysa, basit bir cümle oluştur
    if not desc:
        # Marka + ürün adı + kategoriye dayalı basit bir tanım
        desc_components = []
        if brand:
            desc_components.append(brand)
        if label:
            desc_components.append(label)
        if category_name:
            desc_components.append(category_name.lower())
        # Örneğin: "Apple iPhone 14 Pro Max akıllı telefon"
        base = " ".join(desc_components)
        if base:
            desc = base
            # Ürün tipi cümle formunda değilse sonuna bir fiil tabanlı ifade ekleyebiliriz
            # (örneğin "yüksek performans sunar" gibi) ancak verimiz yoksa eklemiyoruz.
            # Bu noktada sadece temel bir tanım veriyoruz.
    # Trim description to 155 chars limit
    desc = trim_to_limit(desc, 155)
    return desc

def generate_keywords(brand: str, label: str, main_cat: str, cat: str, sub_cat: str, details_text: str) -> str:
    """Ürün için anahtar kelimeler (keywords) listesi üretir."""
    keywords = []
    # 1. Marka + model/ürün adı
    if brand and label:
        keywords.append(f"{brand} {label}")
    elif brand:
        keywords.append(brand)
    elif label:
        keywords.append(label)
    # 2. En spesifik kategori (lowercase, doğal bir terim olarak)
    category_name = sub_cat or cat or main_cat
    if category_name:
        kw_cat = category_name.lower()
        # Marka+model içerisinde kategori kelimesi yoksa ekle
        if kw_cat not in " ".join(keywords).lower():
            keywords.append(kw_cat)
    # 3. Önemli görülen bir özellik (örn. teknik özellik) varsa ekle
    # Basit yaklaşım: details içinde "GB" gibi kapasite birimi geçiyorsa onu anahtar kelime say
    if details_text:
        text_lower = details_text.lower()
        # Örnek kontrol: depolama kapasitesi
        if " gb" in text_lower:
            # '128 gb' gibi bir kısmı ayıkla
            idx = text_lower.find(" gb")
            if idx != -1:
                # Boşluk dahil " gb" bulundu, öncesindeki sayı ile birlikte alalım
                start = idx
                # Geriye doğru rakamları al
                while start > 0 and text_lower[start-1].isdigit():
                    start -= 1
                capacity = details_text[start: idx+3].strip()  # orijinal metinden al, büyük/küçük koru
                if capacity and capacity not in keywords:
                    keywords.append(capacity)
    # Sadece 2-3 anahtar kelime kullan (fazlaysa kırp)
    keywords = keywords[:3]
    return ", ".join(keywords)

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
