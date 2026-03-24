# MediConnect - Inventory Microservice (Atomic)

## Overview
This microservice manages the medication database for the MediConnect platform. 
It uses **Python Flask** and connects to **Google Cloud Firestore** (Firebase) 
to perform CRUD operations on medication items.

## API Endpoints

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| **GET** | `/inventory` | Retrieve all medications. |
| **GET** | `/inventory/<id>` | Retrieve a specific medication by its ID. |
| **POST** | `/inventory` | Add a new medication to the database. |
| **PUT** | `/inventory/<id>` | Restock/update price for a medication. |
| **PUT** | `/inventory/<id>/deduct` | Deduct stock for a specific medication. |

## Setup Requirements
1. **Credentials**: Ensure `serviceAccountKey.json` is present in the `inventory/` folder.
2. **Dependencies**: Handled automatically via Docker.
3. **Port**: Runs on port `5005`.

## How to Run
From the project root directory, run:
```bash
docker-compose up --build