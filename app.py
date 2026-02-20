import json, os, io, tempfile, smtplib, traceback
from flask import Flask, request, send_file, jsonify
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

PDF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i-129.pdf")

SMTP_HOST    = os.environ.get("SMTP_HOST", "")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER    = os.environ.get("SMTP_USER", "")
SMTP_PASS    = os.environ.get("SMTP_PASS", "")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")


def fill_i129(data, input_pdf):
    """Fill the I-129 PDF and return bytes."""
    f = data

    # ── MUST decrypt before append ──────────────────────────────
    reader = PdfReader(input_pdf)
    if reader.is_encrypted:
        reader.decrypt("")

    writer = PdfWriter()
    writer.append(reader)

    # Build key → page-index map from the writer
    key_to_page = {}
    for pi, page in enumerate(writer.pages):
        if "/Annots" not in page:
            continue
        for annot in page["/Annots"]:
            obj = annot.get_object()
            if obj.get("/Subtype") == "/Widget" and "/T" in obj:
                key_to_page[str(obj["/T"])] = pi

    def s(key, value):
        """Set a field value by its short key name."""
        if value is None or value == "" or key not in key_to_page:
            return
        try:
            writer.update_page_form_field_values(
                [writer.pages[key_to_page[key]]], {key: str(value)}, auto_regenerate=False
            )
        except Exception as e:
            app.logger.warning(f"Could not set {key}: {e}")

    # ════════════════════════════════════════════════════════════
    # PAGE 1 — PART 1: PETITIONER INFORMATION
    # ════════════════════════════════════════════════════════════
    if f.get("petitionerType") == "company":
        s("Line3_CompanyorOrgName[0]", f.get("companyName"))
    else:
        s("Line1_FamilyName[0]",  f.get("petLastName"))
        s("Line1_GivenName[0]",   f.get("petFirstName"))
        s("Line1_MiddleName[0]",  f.get("petMiddleName"))

    s("Line7b_StreetNumberName[0]",         f.get("petStreet"))
    s("Line3_AptSteFlrNumber[0]",           f.get("petApt"))
    s("Line_CityTown[0]",                   f.get("petCity"))
    s("P1_Line3_State[0]",                  f.get("petState"))
    s("P1_Line3_ZipCode[0]",                f.get("petZip"))
    s("P1_Line3_Country[0]",                f.get("petCountry") or "United States")
    s("Line2_DaytimePhoneNumber1_Part8[0]", f.get("petPhone"))
    s("Line3_MobilePhoneNumber1_Part8[0]",  f.get("petMobile"))
    s("Line9_EmailAddress[0]",              f.get("petEmail"))
    s("TextField1[0]",                      f.get("fein"))          # FEIN / EIN

    # Nonprofit checkbox (Yes/No)
    nonprofit = f.get("isNonprofit") == "yes"
    s("P1Line6_Yes[0]", "/Y"   if nonprofit else "/Off")
    s("P1Line6_No[0]",  "/Off" if nonprofit else "/Y")

    # ════════════════════════════════════════════════════════════
    # PAGE 2 — PART 2: PETITION INFO  +  PART 3: BENEFICIARY NAME
    # ════════════════════════════════════════════════════════════
    cls_short = {
        "H-1B Specialty Occupation": "H-1B",
        "H-1B1 Chile/Singapore": "H-1B1",
        "H-2A Agricultural Worker": "H-2A",
        "H-2B Non-agricultural Worker": "H-2B",
        "H-3 Trainee": "H-3",
        "L-1A Manager/Executive": "L-1A",
        "L-1B Specialized Knowledge": "L-1B",
        "O-1A Extraordinary Ability (Sciences/Business/Athletics)": "O-1A",
        "O-1B Extraordinary Ability (Arts/TV/Film)": "O-1B",
        "E-1 Treaty Trader": "E-1",
        "E-2 Treaty Investor": "E-2",
        "TN Canada": "TN",
        "TN Mexico": "TN",
        "R-1 Religious Worker": "R-1",
        "Q-1 Cultural Exchange": "Q-1",
    }
    cls = f.get("classification", "")
    s("Part2_ClassificationSymbol[0]", cls_short.get(cls, cls))
    s("TtlNumbersofWorker[0]",         f.get("totalWorkers") or "1")
    s("Line1_ReceiptNumber[0]",        f.get("priorReceiptNumber") or "None")
    s("Line3_TaxNumber[0]",            f.get("fein"))   # also appears on page 2
    s("Line4_SSN[0]",                  f.get("benSSN"))

    # Basis for classification checkboxes
    basis = f.get("basisForClassification", "new")
    basis_map = {
        "new": "new[0]", "concurrent": "concurrent[0]",
        "change_employer": "change[0]", "amended": "amended[0]",
        "change_prev": "previouschange[0]", "continuation": "continuation[0]",
    }
    for k, field in basis_map.items():
        s(field, "/1" if k == basis else "/Off")

    # Requested action (4a checkboxes)
    action_idx = {"notify": 0, "change_extend": 1, "extend": 2, "amend": 3}.get(
        f.get("requestedAction", "notify"), 0
    )
    for i in range(6):
        s(f"P2Checkbox4[{i}]", "/A" if i == action_idx else "/Off")

    # Named beneficiary checkbox
    s("P3Line1_Checkbox[1]", "/Y")
    s("P3Line1_Checkbox[0]", "/Off")

    # Beneficiary name (shown on page 2 as well)
    s("Part3_Line2_FamilyName[0]", f.get("benLastName"))
    s("Part3_Line2_GivenName[0]",  f.get("benFirstName"))
    s("Part3_Line2_MiddleName[0]", f.get("benMiddleName"))

    # ════════════════════════════════════════════════════════════
    # PAGE 3 — PART 3: BENEFICIARY DETAILS  +  PART 4: PROCESSING
    # ════════════════════════════════════════════════════════════
    # Other names used (rows 1-3, we put all in row 1)
    s("Line3_FamilyName1[0]",  f.get("benOtherNames") or "N/A")

    # Gender
    male = f.get("benSex") == "male"
    s("Line1_Gender_P3[0]", "/M"   if male else "/Off")
    s("Line1_Gender_P3[1]", "/Off" if male else "/F")

    s("Line6_DateOfBirth[0]",              f.get("benDob"))
    s("Line5_SSN[0]",                      f.get("benSSN"))
    s("Line1_AlienNumber[0]",              f.get("benANumber"))
    s("Part3Line4_CountryOfBirth[0]",      f.get("benCountryBirth"))
    s("Part4Line3_DProvince[0]",           f.get("benProvinceBirth"))
    s("Part3Line4_CountryOfCitizenship[0]",f.get("benCountryCitizenship"))

    # Passport info
    s("Part3Line5_PassportorTravDoc[0]",   f.get("benPassportNumber"))
    s("Line_CountryOfIssuance[0]",         f.get("benPassportCountry"))
    s("Line11e_ExpDate[0]",                f.get("benPassportIssued"))
    s("Line11e_ExpDate[1]",                f.get("benPassportExpires"))

    # US entry info
    s("Part3Line5_ArrivalDeparture[0]",    f.get("benI94"))
    s("Part3Line5_DateofArrival[0]",       f.get("benLastArrival"))
    s("Line11h_DateStatusExpires[0]",      f.get("benStatusExpires"))
    s("Line5_EAD[0]",                      f.get("benEAD"))
    s("Line5_SEVIS[0]",                    f.get("benSEVIS"))

    # Beneficiary US address
    s("Line8a_StreetNumberName[0]",        f.get("benStreet"))
    s("Line6_AptSteFlrNumber[0]",          f.get("benApt"))
    s("Line8d_CityTown[0]",                f.get("benCity"))
    s("Line8e_State[0]",                   f.get("benState"))
    s("Line8f_ZipCode[0]",                 f.get("benZip"))

    # Part 4: Processing office
    s("OfficeAddressCity[0]",              f.get("consultateCity"))
    s("Part4_1c_State_or_Country[0]",      f.get("consultateCountry"))
    s("TypeofOffice[0]", "/CON")   # Consulate
    s("TypeofOffice[1]", "/Off")
    s("TypeofOffice[2]", "/Off")

    # Valid passport: yes
    s("P4Line2_Checkbox[1]", "/Y")
    s("P4Line2_Checkbox[0]", "/Off")

    # ════════════════════════════════════════════════════════════
    # PAGE 4 — PART 4 continued: more processing questions
    # ════════════════════════════════════════════════════════════
    # Lines 3-11: standard "No" defaults for questions about criminal history etc.
    for line in ["P4Line3","P4Line4","P4Line5","P4Line6","P4Line9","P4Line10","P4Line11a"]:
        s(f"{line}_Yes[0]", "/Off")
        s(f"{line}_No[0]",  "/Y")
    s("P4Line7[0]", "/N")
    s("P4Line7[1]", "/Off")
    s("P4Line8[0]",    "/Off")
    s("P4Line8[1]",    "/Y")
    s("P4Line8a_Yes[0]", "/Off"); s("P4Line8a_No[0]", "/Y")
    s("P4Line8b_Yes[0]", "/Off"); s("P4Line8b_No[0]", "/Y")

    # ════════════════════════════════════════════════════════════
    # PAGE 5 — PART 5: EMPLOYMENT (job, location, schedule)
    # ════════════════════════════════════════════════════════════
    s("Part5_Q1_JobTitle[0]",  f.get("jobTitle"))
    s("Part5_Q2_LCAorETA[0]", f.get("lcaNumber"))

    # Work location (fall back to petitioner address if blank)
    s("P5Line3a_StreetNumberName[0]", f.get("workStreet") or f.get("petStreet"))
    s("P5Line3a_CityTown[0]",         f.get("workCity")   or f.get("petCity"))
    s("P5Line3a_State[0]",            f.get("workState")  or f.get("petState"))
    s("P5Line3a_ZipCode[0]",          f.get("workZip")    or f.get("petZip"))

    # Third-party placement
    third = f.get("isThirdParty") == "yes"
    s("P5Line3[1]", "/1"   if third else "/Off")
    s("P5Line3[0]", "/Off" if third else "/0")
    if third:
        s("P5Line3a_ThirdpartyOrganization[0]", f.get("thirdPartyName"))

    # Itinerary
    itin = f.get("hasItinerary") == "yes"
    s("P5Line4_Yes[0]", "/Y"   if itin else "/Off")
    s("P5Line4_No[0]",  "/Off" if itin else "/Y")

    # Offsite work
    offsite = f.get("isOffsite") == "yes"
    s("P5Line5_Yes[0]", "/Y"   if offsite else "/Off")
    s("P5Line5_No[0]",  "/Off" if offsite else "/Y")

    # CNMI (almost always No)
    s("P5Line6_Yes[0]", "/Off")
    s("P5Line6_No[0]",  "/Y")

    # Full time
    fulltime = f.get("isFullTime") == "yes"
    s("P5Line7_Yes[0]", "/Y"   if fulltime else "/Off")
    s("P5Line7_No[0]",  "/Off" if fulltime else "/Y")
    if not fulltime:
        s("P5Line9_Hours[0]", f.get("hoursPerWeek"))

    # Wages & dates
    s("Line8_Wages[0]",        f.get("wages"))
    s("Line8_Per[0]",          f.get("wagesPer") or "year")
    s("Part5_Q10_DateFrom[0]", f.get("startDate"))
    s("Part5_Q10_DateTo[0]",   f.get("endDate"))

    # ════════════════════════════════════════════════════════════
    # PAGE 6 — PART 5 continued: employer info  +  export control
    # ════════════════════════════════════════════════════════════
    s("Part5Line12_TypeofBusiness[0]", f.get("businessType"))
    s("P5Line13_YearEstablished[0]",   f.get("yearEstablished"))
    s("P5Line14_NumberofEmployees[0]", f.get("numEmployees"))
    s("Line15_GrossAnnualIncome[0]",   f.get("grossIncome"))
    s("Line16_NetAnnualIncome[0]",     f.get("netIncome"))

    try:
        n = int(f.get("numEmployees", "0") or "0")
    except Exception:
        n = 999
    s("P5Line15_CB[0]", "/Y"   if n <= 25 else "/Off")
    s("P5Line15_CB[1]", "/Off" if n <= 25 else "/Y")

    # Export control — no license required (standard)
    s("NoDeemed[0]", "/1")
    s("Deemed[0]",   "/Off")

    # Petitioner name repeated on page 6
    if f.get("petitionerType") == "company":
        s("Line1a_PetitionerLastName[0]",  f.get("companyName"))
        s("Line1a_PetitionerLastName[1]",  f.get("companyName"))
    else:
        s("Line1a_PetitionerLastName[0]",  f.get("petLastName"))
        s("Line1a_PetitionerLastName[1]",  f.get("petLastName"))
        s("Line1b_PetitionerFirstName[0]", f.get("petFirstName"))

    # ════════════════════════════════════════════════════════════
    # PAGE 7 — PART 7: PETITIONER SIGNATURE BLOCK
    # ════════════════════════════════════════════════════════════
    s("Pt7Line3_DaytimePhoneNumber1[0]", f.get("petPhone"))
    s("Pt7Line3_EmailAddress[0]",        f.get("petEmail"))
    # Signature lines left blank intentionally (must be signed by hand)

    # ── Write to buffer ─────────────────────────────────────────
    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def send_email(data, pdf_bytes):
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]):
        return
    try:
        import email.mime.multipart, email.mime.text, email.mime.application
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"]    = SMTP_USER
        msg["To"]      = NOTIFY_EMAIL
        ben = f"{data.get('benFirstName','')} {data.get('benLastName','')}".strip()
        msg["Subject"] = f"I-129 Submission – {ben} | {data.get('classification','')}"
        pet = data.get("companyName","") or f"{data.get('petFirstName','')} {data.get('petLastName','')}".strip()
        body = f"New I-129 submission\n\nPetitioner: {pet}\nBeneficiary: {ben}\nClassification: {data.get('classification','')}\nJob Title: {data.get('jobTitle','')}\nStart: {data.get('startDate','')} → {data.get('endDate','')}\n"
        msg.attach(email.mime.text.MIMEText(body))
        att = email.mime.application.MIMEApplication(pdf_bytes, _subtype="pdf")
        att.add_header("Content-Disposition", "attachment", filename=f"I-129_{ben.replace(' ','_')}.pdf")
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
    return jsonify(
        status="ok",
        pdf_exists=os.path.exists(PDF_PATH),
        pdf_size_mb=round(os.path.getsize(PDF_PATH) / 1024 / 1024, 1) if os.path.exists(PDF_PATH) else 0,
    )


@app.route("/test")
def test():
    sample = {
        "petitionerType": "company", "companyName": "Acme Technology Corp",
        "fein": "12-3456789", "petStreet": "100 Corporate Drive", "petApt": "Suite 400",
        "petCity": "Dallas", "petState": "TX", "petZip": "75201", "petCountry": "United States",
        "petPhone": "2145551234", "petMobile": "2145559999", "petEmail": "hr@acme.com", "isNonprofit": "no",
        "benFirstName": "Rahul", "benLastName": "Sharma", "benMiddleName": "K",
        "benOtherNames": "N/A", "benDob": "1990-05-15", "benSex": "male",
        "benCountryBirth": "India", "benProvinceBirth": "Maharashtra", "benCountryCitizenship": "India",
        "benSSN": "", "benANumber": "", "benPassportNumber": "J1234567",
        "benPassportCountry": "India", "benPassportIssued": "2020-01-10", "benPassportExpires": "2030-01-09",
        "benI94": "12345678901", "benCurrentStatus": "F-1", "benStatusExpires": "2025-05-15",
        "benLastArrival": "2022-08-20", "benEAD": "", "benSEVIS": "N0012345678",
        "benStreet": "500 University Ave", "benApt": "Apt 2B", "benCity": "Dallas", "benState": "TX", "benZip": "75202",
        "jobTitle": "Software Engineer", "lcaNumber": "I-200-24001-123456",
        "workStreet": "", "workCity": "", "workState": "", "workZip": "",
        "isOffsite": "no", "isThirdParty": "no", "thirdPartyName": "",
        "isFullTime": "yes", "hoursPerWeek": "", "wages": "120000", "wagesPer": "year",
        "startDate": "2025-10-01", "endDate": "2028-09-30",
        "businessType": "Information Technology", "yearEstablished": "2005",
        "numEmployees": "500", "grossIncome": "50000000", "netIncome": "8000000", "hasItinerary": "no",
        "classification": "H-1B Specialty Occupation", "basisForClassification": "new",
        "totalWorkers": "1", "priorReceiptNumber": "None",
        "requestedAction": "notify", "consultateCity": "Mumbai", "consultateCountry": "India",
    }
    buf = fill_i129(sample, PDF_PATH)
    return send_file(buf, mimetype="application/pdf", as_attachment=True, download_name="test_i129.pdf")


@app.route("/fill", methods=["POST"])
def fill():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify(error="No JSON data received"), 400

        buf = fill_i129(data, PDF_PATH)
        pdf_bytes = buf.getvalue()

        send_email(data, pdf_bytes)

        ben = f"{data.get('benFirstName','')}_{data.get('benLastName','')}".strip("_") or "form"
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"I-129_{ben}.pdf"
        )
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify(error=str(e)), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
