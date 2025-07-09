# Skoda Data Logger

This project is designed to subscribe to the MySkoda service and offload relevant vehicle information into a local Graylog server. The primary goal is to monitor and log data from a Skoda Enyaq 80 electric vehicle. In the future, the project will include functionality to save charging information into a database to calculate the running costs of the car.

# Very much a vibe-code-thing

So, this is absolutely a "get chatgpt to do the work and see what happens" kind of project. Consider yourself warned.

## Features

- **Event Subscription**: Subscribes to the MySkoda service to receive real-time updates about the vehicle.
- **Graylog Integration**: Logs vehicle data, including charging status, mileage, and position, into a local Graylog server.
- **FastAPI Endpoint**: Provides an HTTP endpoint to view the last 30 lines of the application log.
- **Future Plans**: Save charging data into a database to calculate running costs.

## Requirements

- Python 3.7+
- A local Graylog server
- MySkoda account credentials
- Docker (optional, for containerized deployment)

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/your-username/skoda-data-logger.git
   cd skoda-data-logger

2. Create a virtual environment and activate it:
   ```bash
   python -m venv .venv
   source .venv/bin/activate

4. Install dependencies:
   ```bash
   pip install -r requirements.txt

5. Set up your environment variables: Create a .env file or use your preferred method to set the following environment variables:
   ```bash
   SKODA_USER=<your-myskoda-username>
   SKODA_PASS=<your-myskoda-password>

6. Ensure your Graylog server is running and accessible.

## Usage
1. Start the application:

2. Access the FastAPI endpoint to view the last 30 lines of the application log:
   ```bash
   GET http://localhost:8000/

3. Monitor your Graylog server for incoming logs.

## Project Structure
```
.
├── [main.py](http://_vscodecontentref_/1)                 # Main application logic
├── [requirements.txt](http://_vscodecontentref_/2)        # Python dependencies
├── .github/workflows       # GitHub Actions workflows for CI/CD
│   └── ghcr-image.yml      # Workflow to build and push Docker images
├── README.md               # Project documentation
└── [app.log](http://_vscodecontentref_/3)                 # Application log file (generated at runtime)
```
## Logging
The application logs data to:

Graylog: Logs are sent to a local Graylog server using the graypy library.
File: Logs are also saved to app.log for local debugging.

## FastAPI Endpoint
The application exposes a single endpoint:

GET /: Returns the last 30 lines of the app.log file.
Deployment
Docker
A GitHub Actions workflow (ghcr-image.yml) is included to build and push a Docker image to GitHub Container Registry (GHCR). To deploy using Docker:

Build the Docker image locally:
   ```bash
   docker build -t skoda-data-logger .
   ```
Run the container:
   ```bash
   docker run -d -p 8000:8000 --env SKODA_USER=<your-username> --env SKODA_PASS=<your-password> skoda-data-logger
   ```
CI/CD with GitHub Actions
The ghcr-image.yml workflow automates the process of building and pushing the Docker image to GHCR. It also invokes a deployment webhook after the image is pushed.

## Future Plans
Database Integration: Save charging data into a database to calculate running costs.
Enhanced Analytics: Provide insights into vehicle usage and efficiency.

Contributing
Contributions are welcome! Please open an issue or submit a pull request for any improvements or bug fixes.

License
This project is licensed under the MIT License. See the LICENSE file for details.

Author
Mostly github copilot, and a bit by Tommy Eriksen
