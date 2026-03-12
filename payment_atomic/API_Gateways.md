# Application Programming Interface (API) Gateways - Payment Atomic Microservice

This document tracks the available API endpoints and gateways specifically for the `payment_atomic` microservice.

---

## 1. Payment Microservice (`payment_atomic`)

**Service Description:** Handles atomic operations related to payments, including initialization and recording transactions in Firestore.  
**Base URL:** `http://127.0.0.1:5000` (Local Development)

### `POST /payments`

Creates a new payment record in the system and stores its initial pending state in the database.

*   **URL:** `/payments`
*   **Method:** `POST`
*   **Content-Type:** `application/json`

#### Request Body
```json
{
  "orderID": "integer or string (required)",
  "stripeIntentID": "string (required)",
  "amount": "number/float (required)"
}
```

#### Success Response
*   **Code:** `201 Created`
*   **Content:**
```json
{
  "message": "Payment record created successfully",
  "documentID": "string (Firestore Document ID)"
}
```

#### Error Responses
*   **Code:** `400 Bad Request` (Missing fields or invalid JSON)
*   **Content:**
```json
{
  "error": "Missing required fields. Please provide 'orderID', 'stripeIntentID', and 'amount'."
}
```

*   **Code:** `500 Internal Server Error` (Database write failure)
*   **Content:**
```json
{
  "error": "An error occurred while creating the payment record",
  "details": "string (Exception details)"
}
```

---

*Note: Please update this file as new microservices or endpoints are added to the project.*
