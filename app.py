import json, os, io, traceback, smtplib
from flask import Flask, request, send_file, jsonify
from pypdf import PdfReader, PdfWriter

app = Flask(__name__, static_folder="static", static_url_path="")

@app.after_request
def add_cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    r.headers["Access-Control-Allow-Headers"] = "Content-Type"
    r.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return r

@app.route("/fill", methods=["OPTIONS"])
def fill_options(): return "", 204

PDF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i-129.pdf")
SMTP_HOST = os.environ.get("SMTP_HOST","")
SMTP_PORT = int(os.environ.get("SMTP_PORT","587"))
SMTP_USER = os.environ.get("SMTP_USER","")
SMTP_PASS = os.environ.get("SMTP_PASS","")
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL","")


def fill_i129(data, input_pdf):
    f = data
    reader = PdfReader(input_pdf)
    if reader.is_encrypted:
        reader.decrypt("")

    writer = PdfWriter()
    writer.append(reader)

    # Remove XFA so PDF viewers render AcroForm fields
    if "/AcroForm" in writer._root_object:
        acroform = writer._root_object["/AcroForm"].get_object()
        if "/XFA" in acroform:
            del acroform["/XFA"]

    # Build field → page map
    key_to_page = {}
    for pi, page in enumerate(writer.pages):
        if "/Annots" not in page: continue
        for annot in page["/Annots"]:
            obj = annot.get_object()
            if obj.get("/Subtype") == "/Widget" and "/T" in obj:
                key_to_page[str(obj["/T"])] = pi

    def s(key, value):
        if not value or key not in key_to_page: return
        try:
            writer.update_page_form_field_values(
                [writer.pages[key_to_page[key]]], {key: str(value)}, auto_regenerate=False)
        except Exception as e:
            app.logger.warning(f"Could not set {key}: {e}")

    # ══ PAGE 1 — PART 1: PETITIONER ══════════════════════════════
    if f.get("petitionerType") == "company":
        s("Line3_CompanyorOrgName[0]", f.get("companyName"))
    else:
        s("Line1_FamilyName[0]",  f.get("petLastName"))
        s("Line1_GivenName[0]",   f.get("petFirstName"))
        s("Line1_MiddleName[0]",  f.get("petMiddleName"))

    s("Line7a_InCareofName[0]",             f.get("petInCareOf"))
    s("Line7b_StreetNumberName[0]",         f.get("petStreet"))
    s("Line3_AptSteFlrNumber[0]",           f.get("petApt"))
    s("Line_CityTown[0]",                   f.get("petCity"))
    s("P1_Line3_State[0]",                  f.get("petState"))
    s("P1_Line3_ZipCode[0]",                f.get("petZip"))
    s("P1_Line3_Country[0]",                f.get("petCountry") or "United States")
    s("P1_Line3_Province[0]",               f.get("petProvince"))
    s("P1_Line3_PostalCode[0]",             f.get("petPostalCode"))
    s("Line2_DaytimePhoneNumber1_Part8[0]", f.get("petPhone"))
    s("Line3_MobilePhoneNumber1_Part8[0]",  f.get("petMobile"))
    s("Line9_EmailAddress[0]",              f.get("petEmail"))
    s("TextField1[0]",                      f.get("fein"))
    s("Line3_TaxNumber[0]",                 f.get("petIRS") or f.get("fein"))
    s("Line4_SSN[0]",                       f.get("petSSN"))

    nonprofit = f.get("isNonprofit") == "yes"
    s("P1Line6_Yes[0]", "/Y"   if nonprofit else "/Off")
    s("P1Line6_No[0]",  "/Off" if nonprofit else "/Y")

    # ══ PAGE 2 — PART 2 + PART 3 NAME ════════════════════════════
    cls_short = {
        "H-1B Specialty Occupation":"H-1B","H-1B1 Chile/Singapore":"H-1B1",
        "H-2A Agricultural Worker":"H-2A","H-2B Non-agricultural Worker":"H-2B","H-3 Trainee":"H-3",
        "L-1A Manager/Executive":"L-1A","L-1B Specialized Knowledge":"L-1B",
        "O-1A Extraordinary Ability (Sciences/Business/Athletics)":"O-1A",
        "O-1B Extraordinary Ability (Arts/TV/Film)":"O-1B",
        "E-1 Treaty Trader":"E-1","E-2 Treaty Investor":"E-2",
        "TN Canada":"TN","TN Mexico":"TN","R-1 Religious Worker":"R-1","Q-1 Cultural Exchange":"Q-1",
    }
    cls = f.get("classification","")
    s("Part2_ClassificationSymbol[0]", cls_short.get(cls, cls))
    s("TtlNumbersofWorker[0]",         f.get("totalWorkers") or "1")
    s("Line1_ReceiptNumber[0]",        f.get("priorReceiptNumber") or "None")

    basis_map = {"new":"new[0]","continuation":"continuation[0]","change_prev":"previouschange[0]",
                 "concurrent":"concurrent[0]","change_employer":"change[0]","amended":"amended[0]"}
    basis = f.get("basisForClassification","new")
    for k,field in basis_map.items():
        s(field, "/1" if k==basis else "/Off")

    action_idx = {"notify":0,"change_extend":1,"extend":2,"amend":3}.get(f.get("requestedAction","notify"),0)
    for i in range(6):
        s(f"P2Checkbox4[{i}]", "/A" if i==action_idx else "/Off")

    # Named beneficiary checkbox
    s("P3Line1_Checkbox[1]", "/Y")
    s("P3Line1_Checkbox[0]", "/Off")

    # Beneficiary name on page 2
    s("Part3_Line2_FamilyName[0]", f.get("benLastName"))
    s("Part3_Line2_GivenName[0]",  f.get("benFirstName"))
    s("Part3_Line2_MiddleName[0]", f.get("benMiddleName"))

    # ══ PAGE 3 — PART 3: OTHER NAMES + PERSONAL + US ADDRESS + PART 4 ══
    # Other names (up to 3)
    s("Line3_FamilyName1[0]",  f.get("benOtherLast1"))
    s("Line3_GivenName1[0]",   f.get("benOtherFirst1"))
    s("Line3_MiddleName1[0]",  f.get("benOtherMiddle1"))
    s("Line3_FamilyName2[0]",  f.get("benOtherLast2"))
    s("Line3_GivenName2[0]",   f.get("benOtherFirst2"))
    s("Line3_MiddleName2[0]",  f.get("benOtherMiddle2"))
    s("Line3_FamilyName3[0]",  f.get("benOtherLast3"))
    s("Line3_GivenName3[0]",   f.get("benOtherFirst3"))
    s("Line3_MiddleName3[0]",  f.get("benOtherMiddle3"))

    # Personal info
    male = f.get("benSex") == "male"
    s("Line1_Gender_P3[0]", "/M"   if male else "/Off")
    s("Line1_Gender_P3[1]", "/Off" if male else "/F")
    s("Line6_DateOfBirth[0]",               f.get("benDob"))
    s("Line5_SSN[0]",                       f.get("benSSN"))
    s("Line1_AlienNumber[0]",               f.get("benANumber"))
    s("Part3Line4_CountryOfBirth[0]",       f.get("benCountryBirth"))
    s("Part4Line3_DProvince[0]",            f.get("benProvinceBirth"))
    s("Part3Line4_CountryOfCitizenship[0]", f.get("benCountryCitizenship"))

    # US address
    s("Line8a_StreetNumberName[0]",  f.get("benStreet"))
    s("Line6_AptSteFlrNumber[0]",    f.get("benApt"))
    s("Line8d_CityTown[0]",          f.get("benCity"))
    s("Line8e_State[0]",             f.get("benState"))
    s("Line8f_ZipCode[0]",           f.get("benZip"))

    # Passport + entry info
    s("Part3Line5_PassportorTravDoc[0]", f.get("benPassportNumber"))
    s("Line_CountryOfIssuance[0]",       f.get("benPassportCountry"))
    s("Line11e_ExpDate[1]",              f.get("benPassportIssued"))
    s("Line11e_ExpDate[0]",              f.get("benPassportExpires"))
    s("Line11h_DateStatusExpires[0]",    f.get("benStatusExpires"))
    s("Part3Line5_ArrivalDeparture[0]",  f.get("benI94"))
    s("Part3Line5_DateofArrival[0]",     f.get("benLastArrival"))
    s("Line5_EAD[0]",                    f.get("benEAD"))
    s("Line5_SEVIS[0]",                  f.get("benSEVIS"))

    # Processing office
    s("OfficeAddressCity[0]",        f.get("consultateCity"))
    s("Part4_1c_State_or_Country[0]",f.get("consultateCountry"))
    ot = f.get("officeType","consulate")
    s("TypeofOffice[0]", "/CON" if ot=="consulate" else "/Off")
    s("TypeofOffice[1]", "/PRE" if ot=="preflight" else "/Off")
    s("TypeofOffice[2]", "/POR" if ot=="port"      else "/Off")

    # Beneficiary foreign address
    s("Line2b_StreetNumberName[0]", f.get("benForeignStreet"))
    s("Line2c_CityTown[0]",         f.get("benForeignCity"))
    s("Line2g2_Province[0]",        f.get("benForeignProvince"))
    s("Line3f_PostalCode[0]",        f.get("benForeignPostal"))
    s("Line_Country[0]",             f.get("benForeignCountry"))

    # Page 4 standard answers
    s("P4Line2_Checkbox[1]", "/Y")   # valid passport: yes
    s("P4Line2_Checkbox[0]", "/Off")
    s("P4Line3_No[0]",  "/Y"); s("P4Line3_Yes[0]",  "/Off")
    s("P4Line4_No[0]",  "/Y"); s("P4Line4_Yes[0]",  "/Off")
    s("P4Line5_No[0]",  "/Y"); s("P4Line5_Yes[0]",  "/Off")
    s("P4Line6_No[0]",  "/Y"); s("P4Line6_Yes[0]",  "/Off")
    s("P4Line7[0]",     "/N"); s("P4Line7[1]",       "/Off")
    s("P4Line8[1]",     "/Y"); s("P4Line8[0]",       "/Off")
    s("P4Line8a_No[0]", "/Y"); s("P4Line8a_Yes[0]",  "/Off")
    s("P4Line8b_No[0]", "/Y"); s("P4Line8b_Yes[0]",  "/Off")
    s("P4Line9_No[0]",  "/Y"); s("P4Line9_Yes[0]",   "/Off")
    s("P4Line10_No[0]", "/Y"); s("P4Line10_Yes[0]",  "/Off")
    s("P4Line11a_No[0]","/Y"); s("P4Line11a_Yes[0]", "/Off")

    # ══ PAGE 5 — PART 5: EMPLOYMENT ══════════════════════════════
    s("Part5_Q1_JobTitle[0]",  f.get("jobTitle"))
    s("Part5_Q2_LCAorETA[0]", f.get("lcaNumber"))

    s("P5Line3a_StreetNumberName[0]", f.get("workStreet") or f.get("petStreet"))
    s("P5Line3a_AptSteFlrNumber[0]",  f.get("workApt")    or f.get("petApt"))
    s("P5Line3a_CityTown[0]",         f.get("workCity")   or f.get("petCity"))
    s("P5Line3a_State[0]",            f.get("workState")  or f.get("petState"))
    s("P5Line3a_ZipCode[0]",          f.get("workZip")    or f.get("petZip"))

    third = f.get("isThirdParty") == "yes"
    s("P5Line3[1]", "/1"   if third else "/Off")
    s("P5Line3[0]", "/Off" if third else "/0")
    if third:
        s("P5Line3a_ThirdpartyOrganization[0]", f.get("thirdPartyName"))

    itin = f.get("hasItinerary") == "yes"
    s("P5Line4_Yes[0]", "/Y"   if itin else "/Off")
    s("P5Line4_No[0]",  "/Off" if itin else "/Y")

    offsite = f.get("isOffsite") == "yes"
    s("P5Line5_Yes[0]", "/Y"   if offsite else "/Off")
    s("P5Line5_No[0]",  "/Off" if offsite else "/Y")

    cnmi = f.get("isCNMI") == "yes"
    s("P5Line6_Yes[0]", "/Y"   if cnmi else "/Off")
    s("P5Line6_No[0]",  "/Off" if cnmi else "/Y")

    fulltime = f.get("isFullTime") == "yes"
    s("P5Line7_Yes[0]", "/Y"   if fulltime else "/Off")
    s("P5Line7_No[0]",  "/Off" if fulltime else "/Y")
    if not fulltime:
        s("P5Line9_Hours[0]", f.get("hoursPerWeek"))

    s("Line8_Wages[0]",        f.get("wages"))
    s("Line8_Per[0]",          f.get("wagesPer") or "year")
    s("Line10_Explanation[0]", f.get("otherComp"))
    s("Part5_Q10_DateFrom[0]", f.get("startDate"))
    s("Part5_Q10_DateTo[0]",   f.get("endDate"))

    # ══ PAGE 6 — PART 5: EMPLOYER + PART 6 + PART 7 NAME ════════
    s("Part5Line12_TypeofBusiness[0]", f.get("businessType"))
    s("P5Line13_YearEstablished[0]",   f.get("yearEstablished"))
    s("P5Line14_NumberofEmployees[0]", f.get("numEmployees"))
    s("Line15_GrossAnnualIncome[0]",   f.get("grossIncome"))
    s("Line16_NetAnnualIncome[0]",     f.get("netIncome"))

    few = f.get("has25orFewer") == "yes"
    s("P5Line15_CB[0]", "/Y"   if few else "/Off")
    s("P5Line15_CB[1]", "/Off" if few else "/Y")

    export_lic = f.get("exportControl") == "license_req"
    s("NoDeemed[0]", "/Off" if export_lic else "/1")
    s("Deemed[0]",   "/1"   if export_lic else "/Off")

    # Signatory name repeated on page 6
    s("Line1a_PetitionerLastName[0]",  f.get("sigLastName") or f.get("petLastName") or f.get("companyName"))
    s("Line1a_PetitionerLastName[1]",  f.get("sigTitle"))
    s("Line1b_PetitionerFirstName[0]", f.get("sigFirstName") or f.get("petFirstName"))

    # ══ PAGE 7 — PART 7: SIGNATURE BLOCK ════════════════════════
    s("Pt7Line3_DaytimePhoneNumber1[0]", f.get("sigPhone") or f.get("petPhone"))
    s("Pt7Line3_EmailAddress[0]",        f.get("sigEmail") or f.get("petEmail"))

    # ══ PAGES 13-14 — H CLASSIFICATION SUPPLEMENT ═══════════════
    pet_name = f.get("companyName") or f"{f.get('petFirstName','')} {f.get('petLastName','')}".strip()
    ben_name  = f"{f.get('benFirstName','')} {f.get('benLastName','')}".strip()
    s("Line1_PetitionerName[0]", pet_name)
    s("Line2_BeneficiaryName[0]", ben_name)
    s("Line2_TtlNumberofBeneficiaries[0]", f.get("totalWorkers","1"))

    # H classification checkbox on supplement
    cls_map = {
        "H-1B Specialty Occupation": 0,
        "H-1B1 Chile/Singapore": 1,
        "H-2A Agricultural Worker": 4,
        "H-2B Non-agricultural Worker": 7,
        "H-3 Trainee": 5,
    }
    cls_idx = cls_map.get(f.get("classification",""), -1)
    for i in range(8):
        s(f"SubHLine4_class[{i}]", "/Y" if i == cls_idx else "/Off")

    # Prior H/L stays (up to 6)
    for n in range(1, 7):
        s(f"Name_Line{n}[0]",     f.get(f"hPriorStay{n}Class"))
        s(f"DateFrom_Line{n}[0]", f.get(f"hPriorStay{n}From"))
        s(f"DateTo_Line{n}[0]",   f.get(f"hPriorStay{n}To"))

    # Duties and work experience (page 14)
    s("Line1_Duties[0]",                f.get("hDuties"))
    s("Line2_SummaryofWorkExperience[0]",f.get("hWorkExperience"))

    # H supplement yes/no questions
    guam = f.get("hSubjectToGuam") == "yes"
    s("SupHLine5_Yes[1]", "/Y"   if guam else "/Off")
    s("SupHLine5_No[1]",  "/Off" if guam else "/Y")

    coe = f.get("hChangeOfEmployer") == "yes"
    s("SupHLine5_Yes[0]", "/Y"   if coe else "/Off")
    s("SupHLine5_No[0]",  "/Off" if coe else "/Y")

    ctrl = f.get("hBenControllingInterest") == "yes"
    s("Line8a_Check[0]", "/Y"   if ctrl else "/Off")
    s("Line8a_Check[1]", "/Off" if ctrl else "/Y")
    if ctrl:
        s("Line8b_Explain[0]", f.get("hBenControllingExplain"))

    # Petitioner printed name on supplement signature block
    s("Sect1_PetitionerPrintedName[0]", pet_name)

    # ══ PAGES 21-23 — H-1B DATA COLLECTION SUPPLEMENT ═══════════
    s("Line1_FamilyName[3]", pet_name)
    s("Line1_FamilyName[2]", ben_name)

    # 1a H-1B dependent employer
    dep = f.get("h1bDependentEmployer") == "yes"
    s("H1BSecALine1a_Yes[0]", "/Y"   if dep else "/Off")
    s("H1BSecALine1a_No[0]",  "/Off" if dep else "/Y")

    # 1b willful violator
    wv = f.get("h1bWillfulViolator") == "yes"
    s("H1BSecALine1b_Yes[0]", "/Y"   if wv else "/Off")
    s("H1BSecALine1b_No[0]",  "/Off" if wv else "/Y")

    # 1c exempt from DOL attestation
    exempt = f.get("h1bExemptDOL") == "yes"
    s("H1BSecALine1c_Yes[0]", "/Y"   if exempt else "/Off")
    s("H1BSecALine1c_No[0]",  "/Off" if exempt else "/Y")

    # 1d 50 or more employees
    fifty = f.get("h1b50orMore") == "yes"
    s("H1BSecALine1d_Yes[0]", "/Y"   if fifty else "/Off")
    s("H1BSecALine1d_No[0]",  "/Off" if fifty else "/Y")

    # 1d.1 more than 50% H-1B/L workers
    if fifty:
        pct = f.get("h1bMoreThan50pct") == "yes"
        s("H1BSecALine1d1_Yes[0]", "/Y"   if pct else "/Off")
        s("H1BSecALine1d1_No[0]",  "/Off" if pct else "/Y")

    # 1c.2 exempt from fee (for dependent employers)
    # 1c.1 not used for cap
    if dep:
        c2 = f.get("h1bExemptDOL") == "yes"
        s("H1BSecALine1c1_Yes[0]", "/Y"   if c2 else "/Off")
        s("H1BSecALine1c1_No[0]",  "/Off" if c2 else "/Y")
        s("H1BSecALine1c2_Yes[0]", "/Y"   if c2 else "/Off")
        s("H1BSecALine1c2_No[0]",  "/Off" if c2 else "/Y")

    # Highest education level
    edu_map = {
        "none": "a_no_diploma[0]",
        "hs": "b_HSDiploma[0]",
        "some_college": "c_some_college[0]",
        "associates": "e_AssociateDegree[0]",
        "bachelors": "f_BachelorDegree[0]",
        "some_grad": "d_collegeplus[0]",
        "masters": "g_MasterDegree[0]",
        "professional": "h_ProfessionalDegree[0]",
        "doctorate": "i_DoctorateDegree[0]",
    }
    edu_val = f.get("h1bEducation","")
    for k, field_name in edu_map.items():
        s(field_name, "/Y" if k == edu_val else "/Off")

    # Field of study, DOT, NAICS, rate of pay
    s("PartA_q3_Field_of_Study[0]", f.get("h1bFieldOfStudy"))
    s("Line5_DOTCode[0]",           f.get("h1bDOTCode"))
    s("Line6_NAICSCode[0]",         f.get("h1bNAICSCode"))
    s("Line4_RateofPayPerYear[0]",  f.get("h1bRateOfPay") or f.get("wages"))

    # Section 2 Fee exemption
    fee = f.get("h1bFeeExempt") == "yes"
    s("H1BSec2Line1_Yes[0]", "/Y"   if fee else "/Off")
    s("H1BSec2Line1_No[0]",  "/Off" if fee else "/Y")

    np2 = f.get("h1bNonprofit") == "yes"
    s("H1BSec2Line2_Yes[0]", "/Y"   if np2 else "/Off")
    s("H1BSec2Line2_No[0]",  "/Off" if np2 else "/Y")

    # Default remaining fee exemption lines to No
    for i in range(3, 10):
        s(f"H1BSec2Line{i}_No[0]",  "/Y")
        s(f"H1BSec2Line{i}_Yes[0]", "/Off")

    # Section 3 Cap determination
    cap_e = f.get("h1bCapExempt") == "yes"
    s("Cap[0]", "/Y"   if cap_e else "/Off")   # cap-exempt box 1
    s("Cap[1]", "/Off" if cap_e else "/Off")
    s("Cap[2]", "/Off")
    s("Cap[3]", "/Off" if not f.get("h1bCongressionallyMandated") == "yes" else "/Y")

    buf = io.BytesIO()
    writer.write(buf)
    buf.seek(0)
    return buf


def send_email(data, pdf_bytes):
    if not all([SMTP_HOST, SMTP_USER, SMTP_PASS, NOTIFY_EMAIL]): return
    try:
        import email.mime.multipart, email.mime.text, email.mime.application
        msg = email.mime.multipart.MIMEMultipart()
        msg["From"] = SMTP_USER; msg["To"] = NOTIFY_EMAIL
        ben = f"{data.get('benFirstName','')} {data.get('benLastName','')}".strip()
        msg["Subject"] = f"I-129 Submission – {ben} | {data.get('classification','')}"
        pet = data.get("companyName","") or f"{data.get('petFirstName','')} {data.get('petLastName','')}".strip()
        msg.attach(email.mime.text.MIMEText(f"New I-129\nPetitioner: {pet}\nBeneficiary: {ben}\nClassification: {data.get('classification','')}\nJob: {data.get('jobTitle','')}\nDates: {data.get('startDate','')} to {data.get('endDate','')}\n"))
        att = email.mime.application.MIMEApplication(pdf_bytes, _subtype="pdf")
        att.add_header("Content-Disposition","attachment",filename=f"I-129_{ben.replace(' ','_')}.pdf")
        msg.attach(att)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.starttls(); srv.login(SMTP_USER, SMTP_PASS); srv.send_message(msg)
    except Exception as e:
        app.logger.warning(f"Email failed: {e}")


@app.route("/")
def index(): return app.send_static_file("index.html")

@app.route("/health")
def health():
    return jsonify(status="ok",pdf_exists=os.path.exists(PDF_PATH),
                   pdf_size_mb=round(os.path.getsize(PDF_PATH)/1024/1024,1) if os.path.exists(PDF_PATH) else 0)

@app.route("/test")
def test():
    sample = {"petitionerType":"company","companyName":"Acme Technology Corp","fein":"12-3456789",
      "petStreet":"100 Corporate Drive","petApt":"Suite 400","petCity":"Dallas","petState":"TX",
      "petZip":"75201","petCountry":"United States","petPhone":"2145551234","petEmail":"hr@acme.com",
      "isNonprofit":"no","classification":"H-1B Specialty Occupation","basisForClassification":"new",
      "totalWorkers":"1","priorReceiptNumber":"None","requestedAction":"notify",
      "benLastName":"Sharma","benFirstName":"Rahul","benMiddleName":"K",
      "benDob":"1990-05-15","benSex":"male","benCountryBirth":"India","benProvinceBirth":"Maharashtra",
      "benCountryCitizenship":"India","benSSN":"","benANumber":"",
      "benStreet":"500 University Ave","benApt":"Apt 2B","benCity":"Dallas","benState":"TX","benZip":"75202",
      "benPassportNumber":"J1234567","benPassportCountry":"India",
      "benPassportIssued":"2020-01-10","benPassportExpires":"2030-01-09",
      "benI94":"12345678901","benLastArrival":"2022-08-20",
      "benCurrentStatus":"F-1","benStatusExpires":"2025-05-15","benSEVIS":"N0012345678","benEAD":"",
      "officeType":"consulate","consultateCity":"Mumbai","consultateCountry":"India",
      "jobTitle":"Software Engineer","lcaNumber":"I-200-24001-123456",
      "workStreet":"","workCity":"","workState":"","workZip":"",
      "isThirdParty":"no","hasItinerary":"no","isOffsite":"no","isCNMI":"no",
      "isFullTime":"yes","wages":"120000","wagesPer":"year","startDate":"2025-10-01","endDate":"2028-09-30",
      "businessType":"Information Technology","yearEstablished":"2005","numEmployees":"500",
      "grossIncome":"50000000","netIncome":"8000000","has25orFewer":"no","exportControl":"no_license",
      "sigLastName":"Johnson","sigFirstName":"Sarah","sigTitle":"HR Director",
      "sigPhone":"2145551234","sigEmail":"hr@acme.com"}
    buf = fill_i129(sample, PDF_PATH)
    return send_file(buf,mimetype="application/pdf",as_attachment=True,download_name="test_i129.pdf")

@app.route("/fill", methods=["POST"])
def fill():
    try:
        data = request.get_json(force=True, silent=True)
        if not data: return jsonify(error="No JSON data received"), 400
        buf = fill_i129(data, PDF_PATH)
        pdf_bytes = buf.getvalue()
        send_email(data, pdf_bytes)
        ben = f"{data.get('benFirstName','')}_{data.get('benLastName','')}".strip("_") or "form"
        return send_file(io.BytesIO(pdf_bytes),mimetype="application/pdf",as_attachment=True,download_name=f"I-129_{ben}.pdf")
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify(error=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
