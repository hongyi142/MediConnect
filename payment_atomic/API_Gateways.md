# Application Programming Interface (API) Gateways - Payment Atomic Microservice

This document tracks the available API endpoints and gateways specifically for the `payment_atomic` microservice.

---

## 1. Payment Microservice (`payment_atomic`)

**Service Description:** Handles atomic operations related to payments, including initialization and recording transactions in Firestore.  
**Base URL:** `http://127.0.0.1:5000` (Local Development)

### `POST /payment/create`

Creates a new payment record in the system and stores its initial pending state in the database.

*   **URL:** `/payment/create`
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

### `PUT /payment/<paymentID>`

Updates the status of an existing payment record in the database.

*   **URL:** `/payment/<paymentID>` (replace `<paymentID>` with the actual Firestore Document ID)
*   **Method:** `PUT`
*   **Content-Type:** `application/json`

#### Request Body
```json
{
  "status": "string (required, must be one of: 'pending', 'paid', 'failed')"
}
```

#### Success Response
*   **Code:** `200 OK`
*   **Content:**
```json
{
  "message": "Payment status updated successfully",
  "documentID": "string (Firestore Document ID)",
  "status": "string (e.g., 'paid')"
}
```

#### Error Responses
*   **Code:** `400 Bad Request` (Missing fields, invalid JSON, or invalid status string)
*   **Content (Invalid Status):**
```json
{
  "error": "Invalid status. Must be one of: pending, paid, failed"
}
```

*   **Code:** `404 Not Found` (Document does not exist in Firestore)
*   **Content:**
```json
{
  "error": "Payment record not found."
}
```

*   **Code:** `500 Internal Server Error` (Database read/write failure or other unexpected exception)
*   **Content:**
```json
{
  "error": "An error occurred while updating the payment record",
  "details": "string (Exception details)"
}
```

---

### `GET /payment/<paymentID>`

Retrieves the current status and full record of a specific payment from the database.

*   **URL:** `/payment/<paymentID>` (replace `<paymentID>` with the actual Firestore Document ID)
*   **Method:** `GET`

#### Success Response
*   **Code:** `200 OK`
*   **Content:**
```json
{
  "documentID": "string (Firestore Document ID)",
  "status": "string (e.g., 'pending', 'paid', 'failed')"
}
```

#### Error Responses
*   **Code:** `404 Not Found` (Document does not exist in Firestore)
*   **Content:**
```json
{
  "error": "Payment record not found."
}
```

*   **Code:** `500 Internal Server Error` (Database read failure or other unexpected exception)
*   **Content:**
```json
{
  "error": "An error occurred while fetching the payment record",
  "details": "string (Exception details)"
}
```

---

*Note: Please update this file as new microservices or endpoints are added to the project.*
