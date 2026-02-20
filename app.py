import json, os, tempfile, smtplib
from flask import Flask, request, send_file, jsonify, after_this_request
from pypdf import PdfReader, PdfWriter

app = Flask(__name__, static_folder="static", static_url_path="")

@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response

@app.route("/fill", methods=["OPTIONS"])
def fill_options():
    return "", 204

PDF_PATH = os.path.join(os.path.dirname(__file__), "i-129.pdf")

# ── Optional email config (set as environment variables on Railway/Render) ──
SMTP_HOST     = os.environ.get("SMTP_HOST", "")
SMTP_PORT     = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER     = os.environ.get("SMTP_USER", "")
SMTP_PASS     = os.environ.get("SMTP_PASS", "")
NOTIFY_EMAIL  = os.environ.get("NOTIFY_EMAIL", "")   # where YOU receive submissions


def fill_i129(f, input_pdf, output_pdf):
    reader = PdfReader(input_pdf)
    writer = PdfWriter()
    writer.append(reader)

    key_to_page = {}
    for pi, page in enumerate(writer.pages):
        if "/Annots" not in page:
            continue
        for annot in page["/Annots"]:
            obj = annot.get_object()
            if obj.get("/Subtype") == "/Widget" and "/T" in obj:
                key_to_page[str(obj["/T"])] = pi

    def s(key, value):
        if not value or key not in key_to_page:
            return
        try:
            writer.update_page_form_field_values(
                [writer.pages[key_to_page[key]]], {key: value}, auto_regenerate=False
            )
        except Exception:
            pass

    # Part 1 – Petitioner
    if f.get("petitionerType") == "company":
        s("Line3_CompanyorOrgName[0]", f.get("companyName", ""))
    else:
        s("Line1_FamilyName[0]", f.get("petLastName", ""))
        s("Line1_GivenName[0]",  f.get("petFirstName", ""))
        s("Line1_MiddleName[0]", f.get("petMiddleName", ""))
    s("Line7b_StreetNumberName[0]",        f.get("petStreet", ""))
    s("Line3_AptSteFlrNumber[0]",          f.get("petApt", ""))
    s("Line_CityTown[0]",                  f.get("petCity", ""))
    s("P1_Line3_State[0]",                 f.get("petState", ""))
    s("P1_Line3_ZipCode[0]",               f.get("petZip", ""))
    s("P1_Line3_Country[0]",               f.get("petCountry", "United States"))
    s("Line2_DaytimePhoneNumber1_Part8[0]",f.get("petPhone", ""))
    s("Line3_MobilePhoneNumber1_Part8[0]", f.get("petMobile", ""))
    s("Line9_EmailAddress[0]",             f.get("petEmail", ""))
    s("TextField1[0]",                     f.get("fein", ""))
    s("P1Line6_Yes[0]", "/Y"   if f.get("isNonprofit") == "yes" else "/Off")
    s("P1Line6_No[0]",  "/Off" if f.get("isNonprofit") == "yes" else "/Y")

    # Part 2 – Classification
    cls_map = {
        "H-1B Specialty Occupation":"H-1B","H-1B1 Chile/Singapore":"H-1B1",
        "H-2A Agricultural Worker":"H-2A","H-2B Non-agricultural Worker":"H-2B","H-3 Trainee":"H-3",
        "L-1A Manager/Executive":"L-1A","L-1B Specialized Knowledge":"L-1B",
        "O-1A Extraordinary Ability (Sciences/Business/Athletics)":"O-1A",
        "O-1B Extraordinary Ability (Arts/TV/Film)":"O-1B",
        "E-1 Treaty Trader":"E-1","E-2 Treaty Investor":"E-2",
        "TN Canada":"TN","TN Mexico":"TN","R-1 Religious Worker":"R-1","Q-1 Cultural Exchange":"Q-1",
    }
    cls = f.get("classification", "")
    s("Part2_ClassificationSymbol[0]", cls_map.get(cls, cls))
    s("TtlNumbersofWorker[0]",  f.get("totalWorkers", "1"))
    s("Line1_ReceiptNumber[0]", f.get("priorReceiptNumber", "None"))

    basis = f.get("basisForClassification", "new")
    for k, v in {"new":"new[0]","concurrent":"concurrent[0]","change_employer":"change[0]",
                 "amended":"amended[0]","change_prev":"previouschange[0]","continuation":"continuation[0]"}.items():
        s(v, "/1" if k == basis else "/Off")

    action_i = {"notify":0,"change_extend":1,"extend":2,"amend":3}.get(f.get("requestedAction","notify"),0)
    for i in range(6):
        s(f"P2Checkbox4[{i}]", "/A" if i == action_i else "/Off")

    s("P3Line1_Checkbox[1]", "/Y")
    s("P3Line1_Checkbox[0]", "/Off")
    s("Part3_Line2_FamilyName[0]", f.get("benLastName", ""))
    s("Part3_Line2_GivenName[0]",  f.get("benFirstName", ""))
    s("Part3_Line2_MiddleName[0]", f.get("benMiddleName", ""))

    # Part 3 – Beneficiary
    s("Line3_FamilyName1[0]",              f.get("benOtherNames", ""))
    s("Line1_Gender_P3[0]", "/M"   if f.get("benSex") == "male" else "/Off")
    s("Line1_Gender_P3[1]", "/Off" if f.get("benSex") == "male" else "/F")
    s("Line6_DateOfBirth[0]",              f.get("benDob", ""))
    s("Line5_SSN[0]",                      f.get("benSSN", ""))
    s("Line1_AlienNumber[0]",              f.get("benANumber", ""))
    s("Part3Line4_CountryOfBirth[0]",      f.get("benCountryBirth", ""))
    s("Part4Line3_DProvince[0]",           f.get("benProvinceBirth", ""))
    s("Part3Line4_CountryOfCitizenship[0]",f.get("benCountryCitizenship", ""))
    s("Part3Line5_DateofArrival[0]",       f.get("benLastArrival", ""))
    s("Part3Line5_ArrivalDeparture[0]",    f.get("benI94", ""))
    s("Part3Line5_PassportorTravDoc[0]",   f.get("benPassportNumber", ""))
    s("Line11e_ExpDate[0]",                f.get("benPassportIssued", ""))
    s("Line11e_ExpDate[1]",                f.get("benPassportExpires", ""))
    s("Line_CountryOfIssuance[0]",         f.get("benPassportCountry", ""))
    s("Line11h_DateStatusExpires[0]",      f.get("benStatusExpires", ""))
    s("Line5_SEVIS[0]",                    f.get("benSEVIS", ""))
    s("Line5_EAD[0]",                      f.get("benEAD", ""))
    s("Line8a_StreetNumberName[0]",        f.get("benStreet", ""))
    s("Line6_AptSteFlrNumber[0]",          f.get("benApt", ""))
    s("Line8d_CityTown[0]",                f.get("benCity", ""))
    s("Line8e_State[0]",                   f.get("benState", ""))
    s("Line8f_ZipCode[0]",                 f.get("benZip", ""))
    s("OfficeAddressCity[0]",              f.get("consultateCity", ""))
    s("Part4_1c_State_or_Country[0]",      f.get("consultateCountry", ""))
    s("TypeofOffice[0]", "/CON")
    s("P4Line2_Checkbox[1]", "/Y")
    s("P4Line2_Checkbox[0]", "/Off")

    # Part 5 – Employment
    s("Part5_Q1_JobTitle[0]",  f.get("jobTitle", ""))
    s("Part5_Q2_LCAorETA[0]", f.get("lcaNumber", ""))
    s("P5Line3a_StreetNumberName[0]", f.get("workStreet","") or f.get("petStreet",""))
    s("P5Line3a_CityTown[0]",         f.get("workCity","") or f.get("petCity",""))
    s("P5Line3a_State[0]",            f.get("workState","") or f.get("petState",""))
    s("P5Line3a_ZipCode[0]",          f.get("workZip","") or f.get("petZip",""))
    tp = f.get("isThirdParty") == "yes"
    s("P5Line3[1]", "/1" if tp else "/Off")
    s("P5Line3[0]", "/Off" if tp else "/0")
    if tp:
        s("P5Line3a_ThirdpartyOrganization[0]", f.get("thirdPartyName", ""))
    s("P5Line4_Yes[0]", "/Y"   if f.get("hasItinerary") == "yes" else "/Off")
    s("P5Line4_No[0]",  "/Off" if f.get("hasItinerary") == "yes" else "/Y")
    s("P5Line5_Yes[0]", "/Y"   if f.get("isOffsite") == "yes" else "/Off")
    s("P5Line5_No[0]",  "/Off" if f.get("isOffsite") == "yes" else "/Y")
    s("P5Line6_Yes[0]", "/Off"); s("P5Line6_No[0]", "/Y")
    ft = f.get("isFullTime") == "yes"
    s("P5Line7_Yes[0]", "/Y"   if ft else "/Off")
    s("P5Line7_No[0]",  "/Off" if ft else "/Y")
    if not ft:
        s("P5Line9_Hours[0]", f.get("hoursPerWeek", ""))
    s("Line8_Wages[0]",        f.get("wages", ""))
    s("Line8_Per[0]",          f.get("wagesPer", "year"))
    s("Part5_Q10_DateFrom[0]", f.get("startDate", ""))
    s("Part5_Q10_DateTo[0]",   f.get("endDate", ""))
    s("Part5Line12_TypeofBusiness[0]", f.get("businessType", ""))
    s("P5Line13_YearEstablished[0]",   f.get("yearEstablished", ""))
    s("P5Line14_NumberofEmployees[0]", f.get("numEmployees", ""))
    s("Line15_GrossAnnualIncome[0]",   f.get("grossIncome", ""))
    s("Line16_NetAnnualIncome[0]",     f.get("netIncome", ""))
    try:
        n = int(f.get("numEmployees", "0") or "0")
    except Exception:
        n = 999
    s("P5Line15_CB[0]", "/Y"   if n <= 25 else "/Off")
    s("P5Line15_CB[1]", "/Off" if n <= 25 else "/Y")
    s("NoDeemed[0]", "/1"); s("Deemed[0]", "/Off")

    # Part 7 – Signature block
    if f.get("petitionerType") == "company":
        s("Line1a_PetitionerLastName[0]", f.get("companyName", ""))
        s("Line1a_PetitionerLastName[1]", f.get("companyName", ""))
    else:
        s("Line1a_PetitionerLastName[0]",  f.get("petLastName", ""))
        s("Line1b_PetitionerFirstName[0]", f.get("petFirstName", ""))
    s("Pt7Line3_DaytimePhoneNumber1[0]", f.get("petPhone", ""))
    s("Pt7Line3_EmailAddress[0]",         f.get("petEmail", ""))

    with open(output_pdf, "wb") as out:
        writer.write(out)


def send_notification_email(f, pdf_path):
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]):
        return
    try:
        import email.mime.multipart, email.mime.text, email.mime.application
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"]    = SMTP_USER
        msg["To"]      = NOTIFY_EMAIL
        msg["Subject"] = f"I-129 Submission – {f.get('benFirstName','')} {f.get('benLastName','')} | {f.get('classification','')}"

        pet  = f.get("companyName","") or f"{f.get('petFirstName','')} {f.get('petLastName','')}".strip()
        ben  = f"{f.get('benFirstName','')} {f.get('benLastName','')}".strip()
        body = f"""New I-129 Submission

Petitioner  : {pet}
Beneficiary : {ben}
Classification: {f.get('classification','')}
Job Title   : {f.get('jobTitle','')}
Start Date  : {f.get('startDate','')}
End Date    : {f.get('endDate','')}
Petitioner Email: {f.get('petEmail','')}

The pre-filled I-129 PDF is attached.
"""
        msg.attach(email.mime.text.MIMEText(body))
        with open(pdf_path, "rb") as pf:
            att = email.mime.application.MIMEApplication(pf.read(), _subtype="pdf")
            att.add_header("Content-Disposition", "attachment",
                           filename=f"I-129_{ben.replace(' ','_')}.pdf")
            msg.attach(att)

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.send_message(msg)
    except Exception as e:
        app.logger.warning(f"Email failed: {e}")


@app.route("/")
def index():
    return app.send_static_file("index.html")

@app.route("/health")
def health():
    import os
    return jsonify(
        status="ok",
        pdf_exists=os.path.exists(PDF_PATH),
        pdf_size_mb=round(os.path.getsize(PDF_PATH)/1024/1024, 1) if os.path.exists(PDF_PATH) else 0
    )

@app.route("/test", methods=["GET"])
def test_fill():
    """Quick test — fills with dummy data and returns the PDF."""
    sample = {
        "petitionerType":"company","companyName":"Test Corp","fein":"12-3456789",
        "petStreet":"100 Main St","petCity":"Dallas","petState":"TX","petZip":"75201",
        "petCountry":"United States","petPhone":"2145551234","petEmail":"test@test.com","isNonprofit":"no",
        "benFirstName":"John","benLastName":"Doe","benDob":"1990-01-01","benSex":"male",
        "benCountryBirth":"India","benCountryCitizenship":"India",
        "classification":"H-1B Specialty Occupation","basisForClassification":"new",
        "totalWorkers":"1","requestedAction":"notify","jobTitle":"Software Engineer",
        "wages":"100000","wagesPer":"year","startDate":"2025-10-01","endDate":"2028-09-30",
        "isFullTime":"yes","isOffsite":"no","isThirdParty":"no","hasItinerary":"no",
    }
    import io
    fill_i129(sample, PDF_PATH, "/tmp/_test.pdf")
    with open("/tmp/_test.pdf","rb") as f:
        buf = io.BytesIO(f.read())
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name="test_i129.pdf")


@app.route("/fill", methods=["POST"])
def fill():
    import io, traceback
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify(error="No JSON data received"), 400

        # Fill into a BytesIO buffer — no temp file race condition
        reader = PdfReader(PDF_PATH)
        writer = PdfWriter()
        writer.append(reader)
        fill_i129(data, PDF_PATH, "/tmp/_i129_tmp.pdf")

        with open("/tmp/_i129_tmp.pdf", "rb") as f:
            pdf_bytes = f.read()

        buf = io.BytesIO(pdf_bytes)
        buf.seek(0)

        ben = f"{data.get('benFirstName','')}_{data.get('benLastName','')}".strip("_") or "form"
        return send_file(
            buf,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"I-129_{ben}.pdf"
        )
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify(error=str(e)), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
