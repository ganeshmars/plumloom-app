# Plumloom-App

The productivity platform combining AI chat and document management.

## Prerequisites

Before you begin, ensure you have the following installed on your system:

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

## Setup

1.  **Clone the repository:**

    ```bash
    git clone <repository-url>
    cd plumloom-app
    ```

2.  **Create an environment file:**

    The application uses environment variables for configuration. Copy the example environment file to create your own local configuration:

    ```bash
    cp .env.example .env
    ```

    Then, open the `.env` file and fill in the appropriate values.

    Populate the `.env` file with the necessary values. The `docker-compose.yml` file lists all the required variables, including:

    -   `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
    -   `SECRET_KEY`
    -   `CORS_ORIGINS`
    -   `DESCOPE_PROJECT_ID`, `DESCOPE_MANAGEMENT_KEY`
    -   `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET`
    -   `REDIS_HOST`, `REDIS_PORT`
    -   `GOOGLE_APPLICATION_CREDENTIALS`, `GCS_PROJECT_ID`
    -   `JWT_AI_SECRET`, `JWT_COLLAB_SECRET`
    -   And others as required by the services.

3.  **Google Cloud Credentials:**

    The application requires Google Cloud credentials for its operations. Make sure you have a valid GCP service account JSON key file.

    -   Place your credential file at a known location on your host machine.
    -   Update the `MOUNT_GCP_CREDENTIALS` variable in your `.env` file or directly in the `docker-compose.yml` to point to the directory containing your credentials file. For example:

        ```yaml
        volumes:
          # in docker-compose.yml, under the 'app' service
          - /path/to/your/gcloud/credentials/on/host:/home/appuser/.config/gcloud:ro
        ```

## Running the Application

Once the setup is complete, you can build and run the application using Docker Compose.

1.  **Build and start the services:**

    ```bash
    docker-compose up --build
    ```

    This command will build the Docker images for the services and start all containers. You will see logs from all services in your terminal.

2.  **Running in detached mode:**

    To run the services in the background, use the `-d` flag:

    ```bash
    docker-compose up --build -d
    ```

3.  **Stopping the application:**

    To stop the services, run:

    ```bash
    docker-compose down
    ```

## Database Migrations (Alembic)

The project uses Alembic to manage database migrations. You can run migration commands inside the `app` container.

-   **Create a new migration with auto-generated changes based on models:**
    ```bash
    docker-compose exec app alembic revision --autogenerate -m "description_of_changes"
    ```

-   **Create a new empty migration file:**
    ```bash
    docker-compose exec app alembic revision -m "description_of_changes"
    ```

-   **Apply all migrations to upgrade the database to the latest version:**
    ```bash
    docker-compose exec app alembic upgrade head
    ```

-   **Downgrade the database by one version:**
    ```bash
    docker-compose exec app alembic downgrade -1
    ```

-   **Downgrade to a specific version:**
    ```bash
    docker-compose exec app alembic downgrade <revision_id>
    ```

-   **Show the current migration version:**
    ```bash
    docker-compose exec app alembic current
    ```

-   **Show the migration history:**
    ```bash
    docker-compose exec app alembic history
    ```

-   **Show the SQL that would be executed for a migration (without running it):**
    ```bash
    docker-compose exec app alembic upgrade <revision_id> --sql
    ```

## Services

The `docker-compose.yml` file defines the following services:

-   **`app`**: The main FastAPI application.
    -   Accessible at `http://localhost:5000`
-   **`db`**: The PostgreSQL database.
    -   Accessible on port `5433` on the host machine.
-   **`redis`**: The Redis server for caching and Celery.
    -   Accessible on port `6379` on the host machine.
-   **`celery_sync_worker` & `celery_worker`**: Celery workers for handling background tasks.
-   **`celery_flower`**: A web-based tool for monitoring Celery jobs.
    -   Accessible at `http://localhost:5555`
