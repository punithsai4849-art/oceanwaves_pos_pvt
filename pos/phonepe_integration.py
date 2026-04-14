import hashlib
import base64
import json
import requests
from django.conf import settings
from decouple import config

class PhonePeGateway:
    def __init__(self):
        self.merchant_id = config('PHONEPE_MERCHANT_ID', default='')
        self.salt_key = config('PHONEPE_SALT_KEY', default='')
        self.salt_index = config('PHONEPE_SALT_INDEX', default='1')
        self.env = config('PHONEPE_ENV', default='UAT')
        
        if self.env == 'PROD':
            self.base_url = "https://api.phonepe.com/apis/hermes"
        else:
            self.base_url = "https://api-preprod.phonepe.com/apis/hermes"

    def _generate_checksum(self, payload_base64, endpoint):
        """Generates X-VERIFY header: SHA256(base64 + endpoint + salt_key) + ### + salt_index"""
        string_to_hash = payload_base64 + endpoint + self.salt_key
        sha256_hash = hashlib.sha256(string_to_hash.encode('utf-8')).hexdigest()
        return f"{sha256_hash}###{self.salt_index}"

    def initiate_payment(self, transaction_id, user_id, amount_in_rupees, mobile_number=""):
        """
        Initiates a payment request via PhonePe.
        Returns the response from PhonePe API.
        """
        endpoint = "/pg/v1/pay"
        
        # PhonePe expects amount in PAISE (1 Rupee = 100 Paise)
        amount_in_paise = int(float(amount_in_rupees) * 100)
        
        payload = {
            "merchantId": self.merchant_id,
            "merchantTransactionId": transaction_id,
            "merchantUserId": str(user_id),
            "amount": amount_in_paise,
            "redirectUrl": "https://localhost:8000/callback", # Not strictly needed for POS QR but required by API
            "redirectMode": "POST",
            "callbackUrl": "https://localhost:8000/callback", # Webhook
            "paymentInstrument": {
                "type": "PAY_PAGE" # This will open a payment page or we can use UPI_QR if available
            }
        }
        
        # For POS specifically, we can use "UPI_QR" to get a raw string to display
        # Some accounts support raw UPI_QR type
        # payload["paymentInstrument"] = {"type": "UPI_QR"}

        json_payload = json.dumps(payload)
        payload_base64 = base64.b64encode(json_payload.encode('utf-8')).decode('utf-8')
        
        checksum = self._generate_checksum(payload_base64, endpoint)
        
        headers = {
            "Content-Type": "application/json",
            "X-VERIFY": checksum,
            "accept": "application/json"
        }
        
        response = requests.post(
            f"{self.base_url}{endpoint}",
            json={"request": payload_base64},
            headers=headers
        )
        
        return response.json()

    def check_status(self, transaction_id):
        """
        Checks the status of a transaction.
        Returns the response from PhonePe API.
        """
        endpoint = f"/pg/v1/status/{self.merchant_id}/{transaction_id}"
        
        # For status, the checksum is SHA256(endpoint + salt_key) + ### + salt_index
        string_to_hash = endpoint + self.salt_key
        sha256_hash = hashlib.sha256(string_to_hash.encode('utf-8')).hexdigest()
        checksum = f"{sha256_hash}###{self.salt_index}"
        
        headers = {
            "Content-Type": "application/json",
            "X-VERIFY": checksum,
            "X-MERCHANT-ID": self.merchant_id,
            "accept": "application/json"
        }
        
        response = requests.get(
            f"{self.base_url}{endpoint}",
            headers=headers
        )
        
        return response.json()
