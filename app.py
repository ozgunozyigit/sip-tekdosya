import streamlit as st
import pandas as pd
import re
import math
import os
import calendar
from io import BytesIO
from datetime import datetime, date, timedelta, timezone
from rapidfuzz import fuzz


# =========================
# TÜRKÇE TARİH / RAPOR ARALIĞI
# =========================

TURKCE_AYLAR = {
    1: "Ocak",
    2: "Şubat",
    3: "Mart",
    4: "Nisan",
    5: "Mayıs",
    6: "Haziran",
    7: "Temmuz",
    8: "Ağustos",
    9: "Eylül",
    10: "Ekim",
    11: "Kasım",
    12: "Aralık",
}


def bugun_turkiye():
    # Türkiye UTC+3 sabit saat dilimindedir.
    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()


def ay_ekle(tarih, ay_sayisi):
    yeni_ay_index = tarih.month - 1 + ay_sayisi
    yeni_yil = tarih.year + yeni_ay_index // 12
    yeni_ay = yeni_ay_index % 12 + 1
    yeni_gun = min(tarih.day, calendar.monthrange(yeni_yil, yeni_ay)[1])
    return date(yeni_yil, yeni_ay, yeni_gun)


def turkce_tarih_yaz(tarih):
    return f"{tarih.day:02d} {TURKCE_AYLAR[tarih.month]} {tarih.year}"


def onerilen_rapor_araligi():
    bugun = bugun_turkiye()

    # İçinde bulunulan ayı dahil etmiyoruz.
    # Son tamamlanmış 3 ayı öneriyoruz.
    bu_ayin_ilk_gunu = date(bugun.year, bugun.month, 1)
    baslangic = ay_ekle(bu_ayin_ilk_gunu, -3)
    bitis = bu_ayin_ilk_gunu - timedelta(days=1)

    return baslangic, bitis


# =========================
# ÜRÜN ADI NORMALİZE
# Sistem içi eşleştirme / arama anahtarı
# =========================

def normalize_urun_adi(text):
    if pd.isna(text):
        return ""

    text = str(text)

    # OCR kaynaklı küçük l harfini önce I yap
    text = text.replace("l", "I")

    # Büyük harfe çevir
    text = text.upper()

    replacements = {
        "İ": "I",
        "İ": "I",
        "ı": "I",
        "Ğ": "G",
        "Ü": "U",
        "Ş": "S",
        "Ö": "O",
        "Ç": "C",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    # Noktalama işaretlerini kaldır
    for ch in [".", ",", ";", ":", "/", "\\", "-", "_", "(", ")", "[", "]"]:
        text = text.replace(ch, "")

    # Boşlukları tamamen kaldır
    text = re.sub(r"\s+", "", text)

    # Sayısal alanlarda O harfini 0 yap
    for _ in range(5):
        text = re.sub(r"(\d)O", r"\g<1>0", text)

    return text


# =========================
# OKUNABİLİR NORMALİZE AD
# =========================

def normalize_gorunum(text):
    if pd.isna(text):
        return ""

    text = str(text)

    # Harften sayıya geçişte boşluk
    text = re.sub(r"(?<=[A-Z])(?=\d)", " ", text)

    # Sayıdan harfe geçişte boşluk
    text = re.sub(r"(?<=\d)(?=[A-Z])", " ", text)

    birimler = [
        "MCG", "MG", "ML", "GR", "G",
        "KAPSUL", "TABLET", "FILM", "FLAKON",
        "DOZ", "SASE", "AMPUL", "KREM", "SURUP",
        "DAMLA", "SPREY", "JEL", "MERHEM",
        "SOLUSYON", "SUSP", "COZELTI"
    ]

    for birim in birimler:
        text = re.sub(rf"({birim})(?=[A-Z])", r"\1 ", text)

    text = re.sub(r"\s+", " ", text).strip()

    return text


# =========================
# SAYI OKUMA
# =========================

def to_number(value):
    if pd.isna(value):
        return 0

    if isinstance(value, (int, float)):
        try:
            if math.isnan(value):
                return 0
        except Exception:
            pass
        return float(value)

    value = str(value).strip()

    if value == "":
        return 0

    value = value.replace(",", ".")

    try:
        return float(value)
    except Exception:
        return 0


# =========================
# EKRAN SAYI FORMATLAMA
# 12.00 -> 12
# 12.30 -> 12.3
# 12.345 -> 12.35
# =========================

def sayi_formatla(value):
    if isinstance(value, (int, float)):
        value = round(float(value), 2)

        if value.is_integer():
            return str(int(value))

        return f"{value:.2f}".rstrip("0").rstrip(".")

    return value


# =========================
# SİPARİŞ DURUMU SINIFLANDIRMA
# Sadece ACİL / SİPARİŞ / GEREK YOK
# =========================

def siparis_durumu_belirle(row):
    ortalama_satis = row["ortalama_satis"]
    ham_siparis = row["ham_siparis_miktari"]
    mevcut_stok = row["stok"]

    # ACİL ürün
    # Günlük satış 30 gün üzerinden hesaplanır.
    # 7 günlük stok ihtiyacı = (Ortalama Satış / 30) * 7
    # Ek kural: Stok 30'dan büyükse ACİL olmaz.
    if (
        ortalama_satis > 10
        and ham_siparis > 0
        and mevcut_stok < (ortalama_satis / 30) * 7
        and mevcut_stok <= 30
    ):
        return "ACİL"

    if ham_siparis > 0:
        return "SİPARİŞ"

    return "GEREK YOK"


def siparis_onceligi_belirle(durum):
    priority_map = {
        "ACİL": 1,
        "SİPARİŞ": 2,
        "GEREK YOK": 3
    }

    return priority_map.get(durum, 99)


# =========================
# STREAMLIT TABLO RENKLENDİRME
# Karanlık mod uyumlu
# =========================

def streamlit_satir_renklendir(row):
    durum = row.get("Durum", "")

    if durum == "ACİL":
        return ["background-color: #7A1F1F; color: #FFFFFF; font-weight: 700;"] * len(row)

    if durum == "GEREK YOK":
        return ["background-color: #3F3F46; color: #FFFFFF;"] * len(row)

    return [""] * len(row)


# =========================
# TEK ÜBS DOSYASI OKUMA
# A sütunu: Ürün adı
# B sütunu: 3 aylık toplam satış
# F sütunu: Stok Mik.
# =========================

def oku_ubs_tek_dosya(uploaded_file):
    try:
        df = pd.read_excel(uploaded_file, usecols="A,B,F")
    except Exception as e:
        raise ValueError(
            "Dosya okunamadı. Lütfen ÜBS raporunda A sütununda Ürün Adı, "
            "B sütununda 3 aylık satış, F sütununda Stok Mik. olduğundan emin olun."
        ) from e

    df.columns = ["urun_adi", "toplam_3ay_satis", "stok"]

    df["urun_adi"] = df["urun_adi"].astype(str)
    df["normalize_ad"] = df["urun_adi"].apply(normalize_urun_adi)
    df["toplam_3ay_satis"] = df["toplam_3ay_satis"].apply(to_number)
    df["stok"] = df["stok"].apply(to_number)

    df = df[df["normalize_ad"] != ""]
    df = df[df["normalize_ad"] != "NAN"]

    sonuc = (
        df.groupby("normalize_ad")
        .agg(
            urun_adi=("urun_adi", "first"),
            toplam_3ay_satis=("toplam_3ay_satis", "sum"),
            stok=("stok", "sum")
        )
        .reset_index()
    )

    return sonuc


# =========================
# SİPARİŞ HESAPLAMA
# Tek dosya modeli
# =========================

def siparis_hesapla(ubs_file):
    sonuc = oku_ubs_tek_dosya(ubs_file)

    # Ortalama satış
    sonuc["ortalama_satis"] = sonuc["toplam_3ay_satis"] / 3
    sonuc["ortalama_satis"] = sonuc["ortalama_satis"].round(2)

    # Ham sipariş hesabı
    sonuc["ham_siparis_miktari"] = sonuc["ortalama_satis"] - sonuc["stok"]
    sonuc["ham_siparis_miktari"] = sonuc["ham_siparis_miktari"].apply(
        lambda x: max(0, math.ceil(x))
    )

    # Parti sayısı hesabı
    def parca_sayisi_belirle(ham_siparis):
        if ham_siparis > 300:
            return 7
        elif ham_siparis > 250:
            return 6
        elif ham_siparis > 200:
            return 5
        elif ham_siparis > 150:
            return 4
        elif ham_siparis > 100:
            return 3
        elif ham_siparis > 60:
            return 2
        else:
            return 1

    sonuc["parca_sayisi"] = sonuc["ham_siparis_miktari"].apply(parca_sayisi_belirle)

    # Planlanan parti sipariş hesabı
    sonuc["planlanan_siparis_miktari"] = sonuc.apply(
        lambda row: math.ceil(row["ham_siparis_miktari"] / row["parca_sayisi"])
        if row["ham_siparis_miktari"] > 0 else 0,
        axis=1
    )

    # Görünen normalize ad
    sonuc["normalize_gorunen_ad"] = sonuc["normalize_ad"].apply(normalize_gorunum)

    # Sipariş sınıfı / durum
    sonuc["siparis_durumu"] = sonuc.apply(siparis_durumu_belirle, axis=1)
    sonuc["siparis_onceligi"] = sonuc["siparis_durumu"].apply(siparis_onceligi_belirle)

    # Sayısal kolonları 2 basamağa yuvarla
    sayisal_kolonlar = [
        "planlanan_siparis_miktari",
        "ham_siparis_miktari",
        "parca_sayisi",
        "toplam_3ay_satis",
        "ortalama_satis",
        "stok",
    ]

    for kolon in sayisal_kolonlar:
        sonuc[kolon] = sonuc[kolon].round(2)

    # Öncelik sıralaması
    sonuc = sonuc.sort_values(
        by=["siparis_onceligi", "ham_siparis_miktari", "ortalama_satis"],
        ascending=[True, False, False]
    ).reset_index(drop=True)

    # Kolon sırası
    sonuc = sonuc[
        [
            "normalize_gorunen_ad",
            "planlanan_siparis_miktari",
            "ham_siparis_miktari",
            "parca_sayisi",
            "toplam_3ay_satis",
            "ortalama_satis",
            "stok",
            "siparis_durumu",
            "urun_adi",
        ]
    ]

    sonuc = sonuc.rename(columns={
        "normalize_gorunen_ad": "Ürün Adı",
        "planlanan_siparis_miktari": "Parti Sipariş Mik.",
        "ham_siparis_miktari": "Top. Sipariş Mik.",
        "parca_sayisi": "Parti Sayısı",
        "toplam_3ay_satis": "3 Aylık Satış",
        "ortalama_satis": "Ort. Satış",
        "stok": "Stok",
        "siparis_durumu": "Durum",
        "urun_adi": "Orijinal Ürün Adı",
    })

    return sonuc


# =========================
# EXCEL İNDİRME
# =========================

def excel_indir(df):
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sipariş Sonuç")

        worksheet = writer.sheets["Sipariş Sonuç"]

        from openpyxl.worksheet.table import Table, TableStyleInfo
        from openpyxl.utils import get_column_letter
        from openpyxl.styles import Font, Alignment, PatternFill

        max_row = worksheet.max_row
        max_col = worksheet.max_column

        last_col_letter = get_column_letter(max_col)
        table_range = f"A1:{last_col_letter}{max_row}"

        table = Table(displayName="SiparisSonucTablosu", ref=table_range)

        style = TableStyleInfo(
            name="TableStyleMedium9",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=False,
            showColumnStripes=False
        )

        table.tableStyleInfo = style
        worksheet.add_table(table)

        # Başlıkları kalın ve ortalı yap
        for cell in worksheet[1]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")

        # Sayısal hücre formatlama
        # Tam sayılar: 47
        # Ondalıklı sayılar: 170.33
        for row in worksheet.iter_rows(min_row=2, max_row=max_row):
            for cell in row:
                if isinstance(cell.value, (int, float)):
                    value = round(float(cell.value), 2)

                    if value.is_integer():
                        cell.value = int(value)
                        cell.number_format = "0"
                    else:
                        cell.value = value
                        cell.number_format = "0.00"

        # Durum kolonunu bul
        durum_col_index = None
        for cell in worksheet[1]:
            if cell.value == "Durum":
                durum_col_index = cell.column
                break

        # Excel satır renklendirme
        acil_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
        gerek_yok_fill = PatternFill(start_color="EFEFEF", end_color="EFEFEF", fill_type="solid")

        if durum_col_index is not None:
            for row_num in range(2, max_row + 1):
                durum = worksheet.cell(row=row_num, column=durum_col_index).value

                if durum == "ACİL":
                    fill = acil_fill
                elif durum == "GEREK YOK":
                    fill = gerek_yok_fill
                else:
                    fill = None

                if fill:
                    for col_num in range(1, max_col + 1):
                        worksheet.cell(row=row_num, column=col_num).fill = fill

        # Kolon genişliklerini otomatik ayarla
        for column_cells in worksheet.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter

            for cell in column_cells:
                try:
                    cell_value = str(cell.value) if cell.value is not None else ""
                    if len(cell_value) > max_length:
                        max_length = len(cell_value)
                except Exception:
                    pass

            adjusted_width = min(max_length + 2, 45)
            worksheet.column_dimensions[column_letter].width = adjusted_width

        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = table_range

    return output.getvalue()


# =========================
# PDF İNDİRME
# Print-friendly ACİL sipariş listesi
# Sadece ACİL ürünleri içerir.
# =========================

def pdf_indir(df):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfbase import pdfmetrics
    from xml.sax.saxutils import escape

    output = BytesIO()

    def font_bul():
        font_paths = [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "DejaVuSans.ttf",
        ]

        for path in font_paths:
            if os.path.exists(path):
                return path

        return None

    font_path = font_bul()

    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("TRFont", font_path))
            font_name = "TRFont"
        except Exception:
            font_name = "Helvetica"
    else:
        font_name = "Helvetica"

    pdf = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=0.8 * cm,
        leftMargin=0.8 * cm,
        topMargin=0.8 * cm,
        bottomMargin=0.8 * cm
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "TitleTR",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=15,
        leading=18,
        alignment=1,
        spaceAfter=8
    )

    normal_style = ParagraphStyle(
        "NormalTR",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=7.2,
        leading=8.6
    )

    header_style = ParagraphStyle(
        "HeaderTR",
        parent=styles["Normal"],
        fontName=font_name,
        fontSize=7.2,
        leading=8.6,
        alignment=1
    )

    elements = []

    pdf_df = df[df["Durum"] == "ACİL"].copy()

    pdf_df = pdf_df[
        [
            "Ürün Adı",
            "Parti Sipariş Mik.",
            "Top. Sipariş Mik.",
            "Ort. Satış",
            "Stok",
        ]
    ]

    toplam_urun = len(pdf_df)
    tarih = datetime.now().strftime("%d.%m.%Y %H:%M")

    elements.append(Paragraph("ACİL SİPARİŞ LİSTESİ", title_style))
    elements.append(
        Paragraph(
            f"Tarih: {tarih} &nbsp;&nbsp; | &nbsp;&nbsp; "
            f"Acil Sipariş Ürün Sayısı: {toplam_urun}",
            normal_style
        )
    )
    elements.append(Spacer(1, 8))

    def sayi_pdf_formatla(value):
        try:
            value = round(float(value), 2)
            if value.is_integer():
                return str(int(value))
            return f"{value:.2f}".rstrip("0").rstrip(".")
        except Exception:
            return str(value)

    table_data = [
        [
            Paragraph("Ürün Adı", header_style),
            Paragraph("Parti Sip. Mik.", header_style),
            Paragraph("Top. Sip. Mik.", header_style),
            Paragraph("Ort. Satış", header_style),
            Paragraph("Stok", header_style),
        ]
    ]

    for _, row in pdf_df.iterrows():
        table_data.append(
            [
                Paragraph(escape(str(row["Ürün Adı"])), normal_style),
                sayi_pdf_formatla(row["Parti Sipariş Mik."]),
                sayi_pdf_formatla(row["Top. Sipariş Mik."]),
                sayi_pdf_formatla(row["Ort. Satış"]),
                sayi_pdf_formatla(row["Stok"]),
            ]
        )

    table = Table(
        table_data,
        repeatRows=1,
        colWidths=[
            8.6 * cm,
            2.7 * cm,
            2.7 * cm,
            2.3 * cm,
            1.8 * cm,
        ]
    )

    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), font_name),
                ("FONTSIZE", (0, 0), (-1, -1), 7.2),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 1), (-1, -1), "CENTER"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    elements.append(table)

    def sayfa_numarasi(canvas, doc):
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.drawRightString(20.0 * cm, 0.5 * cm, f"Sayfa {doc.page}")
        canvas.restoreState()

    pdf.build(elements, onFirstPage=sayfa_numarasi, onLaterPages=sayfa_numarasi)

    return output.getvalue()


# =========================
# WEB ARAYÜZ
# =========================

st.set_page_config(
    page_title="Eczane Sipariş Motoru",
    layout="wide"
)

st.title("Eczane Sipariş Motoru")

rapor_baslangic, rapor_bitis = onerilen_rapor_araligi()
rapor_araligi_metni = f"{turkce_tarih_yaz(rapor_baslangic)} - {turkce_tarih_yaz(rapor_bitis)}"

st.subheader("📌 Kullanım Kılavuzu")

with st.expander("Kullanım kılavuzunu görüntüle", expanded=True):
    st.markdown("### ✅ ÜBS Satış Raporu")
    st.info(
        f"📅 Önerilen Rapor Tarih Aralığı: **{rapor_araligi_metni}**"
    )

    st.write(
        "Eczanem programından geçmiş 3 tamamlanmış ayı kapsayan "
        "“Ürün Bazında Satış” raporunu tek Excel dosyası olarak kaydedin."
    )

    st.write(
        "Raporu sipariş vereceğiniz gün oluşturun. "
        "Dosyanın içindeki **Stok Mik.** kolonu güncel stok olarak kullanılacaktır."
    )

    st.warning(
        "Dosya yapısı: A sütunu Ürün Adı, B sütunu 3 aylık toplam satış, "
        "F sütunu Stok Mik. olmalıdır."
    )

col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("Satış ve Stok Dosyası")
    ubs_file = st.file_uploader(
        "3 Aylık ÜBS Ürün Bazında Satış Dosyası",
        type=["xls", "xlsx"]
    )

with col2:
    st.subheader("Rapor Bilgisi")
    st.info(
        f"Bu ay için önerilen rapor aralığı:\n\n"
        f"**{rapor_araligi_metni}**"
    )

st.divider()

tum_dosyalar_yuklendi = bool(ubs_file)

if not tum_dosyalar_yuklendi:
    st.info("Sipariş hazırlamak için lütfen 3 aylık ÜBS satış dosyasını yükleyin.")
else:
    buton_col1, buton_col2, buton_col3 = st.columns([1, 1, 1])

    with buton_col1:
        if st.button("Siparişi Hazırla", type="primary"):
            try:
                sonuc = siparis_hesapla(ubs_file)
                st.session_state["sonuc"] = sonuc
            except Exception as e:
                st.error(f"Hesaplama sırasında hata oluştu: {e}")

    if "sonuc" in st.session_state:
        with buton_col2:
            excel_data = excel_indir(st.session_state["sonuc"])

            st.download_button(
                label="Excel İndir",
                data=excel_data,
                file_name="siparis_sonuc.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        with buton_col3:
            pdf_data = pdf_indir(st.session_state["sonuc"])

            st.download_button(
                label="Acil Sipariş PDF İndir",
                data=pdf_data,
                file_name="acil_siparis_listesi.pdf",
                mime="application/pdf"
            )

if "sonuc" in st.session_state:
    sonuc = st.session_state["sonuc"]

    st.success("Hesaplama tamamlandı.")

    toplam_acil = len(sonuc[sonuc["Durum"] == "ACİL"])
    toplam_siparis = len(sonuc[sonuc["Durum"] == "SİPARİŞ"])
    toplam_gerek_yok = len(sonuc[sonuc["Durum"] == "GEREK YOK"])

    kpi1, kpi2, kpi3 = st.columns(3)

    kpi1.metric("ACİL", toplam_acil)
    kpi2.metric("SİPARİŞ", toplam_siparis)
    kpi3.metric("GEREK YOK", toplam_gerek_yok)

    arama = st.text_input("Ürün adı ara")

    if arama:
        arama_key = normalize_urun_adi(arama)
        arama_upper = arama.upper()

        sonuc_goster = sonuc.copy()

        def arama_skoru(row):
            urun_adi_gorunen = str(row["Ürün Adı"])
            orijinal_urun_adi = str(row["Orijinal Ürün Adı"])

            normalize_key = normalize_urun_adi(urun_adi_gorunen)
            orijinal_key = normalize_urun_adi(orijinal_urun_adi)

            # 1. İlk harflerden başlayan ürünleri direkt yakala
            if len(arama_key) >= 3:
                if normalize_key.startswith(arama_key) or orijinal_key.startswith(arama_key):
                    return 100

            # 2. Orijinal ürün adının herhangi bir kelimesi aranan değerle başlıyorsa yakala
            urun_kelime_keyleri = [
                normalize_urun_adi(kelime)
                for kelime in orijinal_urun_adi.split()
                if kelime.strip() != ""
            ]

            for kelime_key in urun_kelime_keyleri:
                if len(arama_key) >= 3 and kelime_key.startswith(arama_key):
                    return 98

            # 3. Direkt içeriyorsa yüksek skor ver
            if arama_key in normalize_key or arama_key in orijinal_key:
                return 95

            if arama_upper in orijinal_urun_adi.upper():
                return 95

            # 4. Fuzzy skorlar
            skor1 = fuzz.partial_ratio(normalize_key, arama_key)
            skor2 = fuzz.partial_ratio(orijinal_key, arama_key)
            skor3 = fuzz.token_set_ratio(normalize_key, arama_key)
            skor4 = fuzz.token_set_ratio(orijinal_key, arama_key)

            return max(skor1, skor2, skor3, skor4)

        sonuc_goster["Arama Skoru"] = sonuc_goster.apply(arama_skoru, axis=1)

        filtre = sonuc_goster[sonuc_goster["Arama Skoru"] >= 55].copy()
        filtre = filtre.sort_values(
            ["Arama Skoru", "Top. Sipariş Mik."],
            ascending=[False, False]
        )

    else:
        filtre = sonuc.copy()
        filtre["Arama Skoru"] = ""

    filtre_gorunum = filtre.drop(columns=["Arama Skoru"], errors="ignore")

    st.dataframe(
        filtre_gorunum.style
        .apply(streamlit_satir_renklendir, axis=1)
        .format(sayi_formatla),
        use_container_width=True,
        hide_index=True
    )