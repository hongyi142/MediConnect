# Application Programming Interface (API) Gateways - Payment Wrapper Microservice

This document tracks the available API endpoints and gateways specifically for the `payment_wrapper` microservice.

---

## 1. Payment Wrapper Microservice (`payment_wrapper`)

**Service Description:** A synchronous HTTP REST API acting as a bridge to Stripe, facilitating Checkout Sessions without a localized database.
**Base URL:** `http://127.0.0.1:5001` (Local Development)

### `POST /payment/checkout`

Creates a Stripe Checkout Session for processing a payment and returns the checkout URL.

*   **URL:** `/payment/checkout`
*   **Method:** `POST`
*   **Content-Type:** `application/json`

#### Request Body
```json
{
  "order_id": "string (required, unique identifier for the order)",
  "item_name": "string (required, descriptive name of the item/service)",
  "amount": 1000, 
  "currency": "string (optional, defaults to 'sgd')"
}
```
*Note: `amount` is an integer denoting the amount in the smallest currency unit (e.g., 1000 = $10.00 SGD).*

#### Success Response
*   **Code:** `200 OK`
*   **Content:**
```json
{
  "checkout_url": "string (The URL to redirect the user to Stripe Checkout)",
  "session_id": "string (The Stripe Checkout Session ID)"
}
```

#### Error Responses
*   **Code:** `400 Bad Request` (Missing fields or invalid JSON)
*   **Content:**
```json
{
  "error": "Missing required fields. Please provide 'order_id', 'item_name', and 'amount'."
}
```

*   **Code:** `400 Bad Request` (Stripe Error)
*   **Content:**
```json
{
  "error": "A Stripe error occurred",
  "details": "string (Exception details from Stripe)"
}
```

*   **Code:** `500 Internal Server Error` (Unexpected error)
*   **Content:**
```json
{
  "error": "An internal server error occurred",
  "details": "string (Exception details)"
}
```

---

### `GET /payment/status/<session_id>`

Retrieves the live payment status of a Stripe Checkout Session.

*   **URL:** `/payment/status/<session_id>` (replace `<session_id>` with the actual Stripe Session ID)
*   **Method:** `GET`

#### Success Response
*   **Code:** `200 OK`
*   **Content:**
```json
{
  "payment_status": "string (e.g., 'unpaid', 'paid', 'no_payment_required')",
  "order_id": "string (The original client_reference_id)"
}
```

#### Error Responses
*   **Code:** `400 Bad Request` (Stripe Error, session not found, etc.)
*   **Content:**
```json
{
  "error": "A Stripe error occurred while retrieving the session",
  "details": "string (Exception details from Stripe)"
}
```

*   **Code:** `500 Internal Server Error` (Unexpected error)
*   **Content:**
```json
{
  "error": "An internal server error occurred",
  "details": "string (Exception details)"
}
```

---

*Last Updated: 2026-03-14*
