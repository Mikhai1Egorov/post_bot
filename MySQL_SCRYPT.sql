SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS neo_content_pipeline
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE neo_content_pipeline;

DROP TABLE IF EXISTS user_actions;
DROP TABLE IF EXISTS publications;
DROP TABLE IF EXISTS approval_batch_items;
DROP TABLE IF EXISTS approval_batches;
DROP TABLE IF EXISTS task_artifacts;
DROP TABLE IF EXISTS task_renders;
DROP TABLE IF EXISTS task_generations;
DROP TABLE IF EXISTS task_research_sources;
DROP TABLE IF EXISTS task_status_history;
DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS upload_validation_errors;
DROP TABLE IF EXISTS article_balance_ledger;
DROP TABLE IF EXISTS user_article_balances;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS article_packages;
DROP TABLE IF EXISTS uploads;
DROP TABLE IF EXISTS users;

SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    telegram_user_id BIGINT NOT NULL,
    interface_language VARCHAR(10) NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_telegram_user_id (telegram_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE uploads (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    template_version VARCHAR(50) NULL,

    upload_status ENUM(
        'RECEIVED',
        'VALIDATED',
        'VALIDATION_FAILED',
        'PROCESSING',
        'COMPLETED',
        'FAILED',
        'CANCELLED'
    ) NOT NULL DEFAULT 'RECEIVED',

    total_rows_count INT NOT NULL DEFAULT 0,
    valid_rows_count INT NOT NULL DEFAULT 0,
    invalid_rows_count INT NOT NULL DEFAULT 0,

    required_articles_count INT NOT NULL DEFAULT 0,
    reserved_articles_count INT NOT NULL DEFAULT 0,

    billing_status ENUM(
        'PENDING',
        'REJECTED',
        'RESERVED',
        'CONSUMED',
        'RELEASED'
    ) NOT NULL DEFAULT 'PENDING',

    billing_checked_at DATETIME NULL,
    billing_error_message TEXT NULL,

    received_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    validated_at DATETIME NULL,
    processing_started_at DATETIME NULL,
    completed_at DATETIME NULL,
    last_error_message TEXT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_uploads_user_id (user_id),
    KEY idx_uploads_upload_status (upload_status),
    KEY idx_uploads_billing_status (billing_status),
    KEY idx_uploads_received_at (received_at),
    CONSTRAINT fk_uploads_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE article_packages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    package_code VARCHAR(50) NOT NULL,
    articles_qty INT NOT NULL,
    price_amount DECIMAL(10,2) NULL,
    currency_code VARCHAR(10) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_article_packages_package_code (package_code),
    KEY idx_article_packages_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE payments (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    package_id BIGINT UNSIGNED NOT NULL,

    provider_code VARCHAR(50) NOT NULL,
    provider_payment_id VARCHAR(255) NULL,
    provider_invoice_id VARCHAR(255) NULL,

    payment_status ENUM(
        'PENDING',
        'PAID',
        'FAILED',
        'CANCELLED',
        'REFUNDED'
    ) NOT NULL DEFAULT 'PENDING',

    amount_value DECIMAL(10,2) NULL,
    currency_code VARCHAR(10) NULL,
    purchased_articles_qty INT NOT NULL,

    raw_payload_json JSON NULL,

    paid_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    UNIQUE KEY uk_payments_provider_payment_id (provider_payment_id),
    KEY idx_payments_user_id (user_id),
    KEY idx_payments_package_id (package_id),
    KEY idx_payments_payment_status (payment_status),
    CONSTRAINT fk_payments_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT,
    CONSTRAINT fk_payments_package
        FOREIGN KEY (package_id) REFERENCES article_packages(id)
        ON DELETE RESTRICT
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE user_article_balances (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    available_articles_count INT NOT NULL DEFAULT 0,
    reserved_articles_count INT NOT NULL DEFAULT 0,
    consumed_articles_total INT NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_article_balances_user_id (user_id),
    KEY idx_user_article_balances_available_articles_count (available_articles_count),
    CONSTRAINT fk_user_article_balances_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE upload_validation_errors (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    upload_id BIGINT UNSIGNED NOT NULL,
    sheet_name VARCHAR(100) NULL,
    excel_row INT NOT NULL,
    column_name VARCHAR(100) NOT NULL,
    error_code VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    bad_value TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_upload_validation_errors_upload_id (upload_id),
    KEY idx_upload_validation_errors_excel_row (excel_row),
    CONSTRAINT fk_upload_validation_errors_upload
        FOREIGN KEY (upload_id) REFERENCES uploads(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE tasks (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    upload_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,

    target_channel VARCHAR(255) NOT NULL,
    topic_text TEXT NOT NULL,
    custom_title TEXT NULL,
    keywords_text TEXT NULL,

    source_time_range ENUM(
        '24h',
        '3d',
        '7d',
        '30d'
    ) NULL,

    source_language_code ENUM(
        'en',
        'ru',
        'uk',
        'es',
        'zh',
        'hi',
        'ar'
    ) NULL,

    response_language_code ENUM(
        'en',
        'ru',
        'uk',
        'es',
        'zh',
        'hi',
        'ar'
    ) NOT NULL,

    style_code ENUM(
        'journalistic',
        'simple',
        'expert'
    ) NULL,

    content_length_code ENUM(
        'short',
        'medium',
        'long'
    ) NULL,

    include_image_flag TINYINT(1) NOT NULL DEFAULT 0,
    footer_text TEXT NULL,
    footer_link_url TEXT NULL,
    scheduled_publish_at DATETIME NULL,
    publish_mode ENUM(
        'instant',
        'approval'
    ) NOT NULL,

    article_cost INT NOT NULL DEFAULT 1,
    billing_state ENUM(
        'RESERVED',
        'CONSUMED',
        'RELEASED'
    ) NOT NULL DEFAULT 'RESERVED',

    task_status ENUM(
        'CREATED',
        'QUEUED',
        'PREPARING',
        'RESEARCHING',
        'GENERATING',
        'RENDERING',
        'READY_FOR_APPROVAL',
        'PUBLISHING',
        'DONE',
        'FAILED',
        'CANCELLED'
    ) NOT NULL DEFAULT 'CREATED',

    priority_value INT NULL,
    dedupe_hash VARCHAR(128) NULL,
    retry_count INT NOT NULL DEFAULT 0,
    last_error_message TEXT NULL,

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,

    PRIMARY KEY (id),
    KEY idx_tasks_upload_id (upload_id),
    KEY idx_tasks_user_id (user_id),
    KEY idx_tasks_task_status (task_status),
    KEY idx_tasks_publish_mode (publish_mode),
    KEY idx_tasks_style_code (style_code),
    KEY idx_tasks_response_language_code (response_language_code),
    KEY idx_tasks_scheduled_publish_at (scheduled_publish_at),
    KEY idx_tasks_billing_state (billing_state),
    KEY idx_tasks_dedupe_hash (dedupe_hash),
    CONSTRAINT fk_tasks_upload
        FOREIGN KEY (upload_id) REFERENCES uploads(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT,
    CONSTRAINT fk_tasks_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE article_balance_ledger (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    payment_id BIGINT UNSIGNED NULL,
    upload_id BIGINT UNSIGNED NULL,
    task_id BIGINT UNSIGNED NULL,

    entry_type ENUM(
        'PURCHASE',
        'RESERVE',
        'RELEASE',
        'CONSUME',
        'REFUND',
        'MANUAL_ADJUSTMENT',
        'CORRECTION'
    ) NOT NULL,

    articles_delta INT NOT NULL,
    balance_after INT NULL,
    note_text TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_article_balance_ledger_user_id (user_id),
    KEY idx_article_balance_ledger_payment_id (payment_id),
    KEY idx_article_balance_ledger_upload_id (upload_id),
    KEY idx_article_balance_ledger_task_id (task_id),
    KEY idx_article_balance_ledger_entry_type (entry_type),
    KEY idx_article_balance_ledger_created_at (created_at),
    CONSTRAINT fk_article_balance_ledger_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT,
    CONSTRAINT fk_article_balance_ledger_payment
        FOREIGN KEY (payment_id) REFERENCES payments(id)
        ON DELETE SET NULL
        ON UPDATE RESTRICT,
    CONSTRAINT fk_article_balance_ledger_upload
        FOREIGN KEY (upload_id) REFERENCES uploads(id)
        ON DELETE SET NULL
        ON UPDATE RESTRICT,
    CONSTRAINT fk_article_balance_ledger_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE SET NULL
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE task_status_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,
    old_status ENUM(
        'CREATED',
        'QUEUED',
        'PREPARING',
        'RESEARCHING',
        'GENERATING',
        'RENDERING',
        'READY_FOR_APPROVAL',
        'PUBLISHING',
        'DONE',
        'FAILED',
        'CANCELLED'
    ) NULL,
    new_status ENUM(
        'CREATED',
        'QUEUED',
        'PREPARING',
        'RESEARCHING',
        'GENERATING',
        'RENDERING',
        'READY_FOR_APPROVAL',
        'PUBLISHING',
        'DONE',
        'FAILED',
        'CANCELLED'
    ) NOT NULL,
    changed_by VARCHAR(30) NOT NULL,
    change_note TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_task_status_history_task_id (task_id),
    KEY idx_task_status_history_new_status (new_status),
    KEY idx_task_status_history_created_at (created_at),
    CONSTRAINT fk_task_status_history_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE task_research_sources (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT NULL,
    source_language_code ENUM(
        'en',
        'ru',
        'uk',
        'es',
        'zh',
        'hi',
        'ar'
    ) NULL,
    published_at DATETIME NULL,
    collected_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_payload_json JSON NULL,
    PRIMARY KEY (id),
    KEY idx_task_research_sources_task_id (task_id),
    KEY idx_task_research_sources_collected_at (collected_at),
    CONSTRAINT fk_task_research_sources_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE task_generations (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,

    prompt_template_key VARCHAR(100) NULL,
    prompt_template_version VARCHAR(50) NULL,
    system_instructions_version VARCHAR(50) NULL,
    model_name VARCHAR(100) NOT NULL,

    final_prompt_text LONGTEXT NULL,
    research_context_text LONGTEXT NULL,
    raw_output_text LONGTEXT NULL,

    generation_status ENUM(
        'STARTED',
        'SUCCEEDED',
        'FAILED'
    ) NOT NULL DEFAULT 'STARTED',

    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at DATETIME NULL,
    error_code VARCHAR(100) NULL,
    error_message TEXT NULL,
    retryable TINYINT(1) NOT NULL DEFAULT 0,

    PRIMARY KEY (id),
    KEY idx_task_generations_task_id (task_id),
    KEY idx_task_generations_generation_status (generation_status),
    KEY idx_task_generations_started_at (started_at),
    CONSTRAINT fk_task_generations_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE task_renders (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,

    final_title_text TEXT NULL,
    body_html LONGTEXT NULL,
    preview_text TEXT NULL,
    slug_value VARCHAR(255) NULL,
    html_storage_path VARCHAR(1024) NULL,
    error_code VARCHAR(100) NULL,
    error_message TEXT NULL,

    render_status ENUM(
        'STARTED',
        'SUCCEEDED',
        'FAILED'
    ) NOT NULL DEFAULT 'STARTED',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_task_renders_task_id (task_id),
    KEY idx_task_renders_render_status (render_status),
    KEY idx_task_renders_slug_value (slug_value),
    CONSTRAINT fk_task_renders_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE task_artifacts (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NULL,
    upload_id BIGINT UNSIGNED NULL,

    artifact_type ENUM(
        'SOURCE_XLSX',
        'HTML',
        'ZIP',
        'PREVIEW'
    ) NOT NULL,

    storage_path VARCHAR(1024) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    size_bytes BIGINT NULL,
    is_final TINYINT(1) NOT NULL DEFAULT 0,
    expires_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_task_artifacts_task_id (task_id),
    KEY idx_task_artifacts_upload_id (upload_id),
    KEY idx_task_artifacts_artifact_type (artifact_type),
    KEY idx_task_artifacts_expires_at (expires_at),
    CONSTRAINT fk_task_artifacts_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT,
    CONSTRAINT fk_task_artifacts_upload
        FOREIGN KEY (upload_id) REFERENCES uploads(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE approval_batches (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    upload_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,

    batch_status ENUM(
        'READY',
        'USER_NOTIFIED',
        'PUBLISHED',
        'DOWNLOADED',
        'EXPIRED'
    ) NOT NULL DEFAULT 'READY',

    zip_artifact_id BIGINT UNSIGNED NULL,

    notified_at DATETIME NULL,
    published_at DATETIME NULL,
    downloaded_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_approval_batches_upload_id (upload_id),
    KEY idx_approval_batches_user_id (user_id),
    KEY idx_approval_batches_batch_status (batch_status),
    CONSTRAINT fk_approval_batches_upload
        FOREIGN KEY (upload_id) REFERENCES uploads(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT,
    CONSTRAINT fk_approval_batches_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT,
    CONSTRAINT fk_approval_batches_zip_artifact
        FOREIGN KEY (zip_artifact_id) REFERENCES task_artifacts(id)
        ON DELETE SET NULL
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE approval_batch_items (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    batch_id BIGINT UNSIGNED NOT NULL,
    task_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_approval_batch_items_batch_task (batch_id, task_id),
    KEY idx_approval_batch_items_task_id (task_id),
    CONSTRAINT fk_approval_batch_items_batch
        FOREIGN KEY (batch_id) REFERENCES approval_batches(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT,
    CONSTRAINT fk_approval_batch_items_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE publications (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    task_id BIGINT UNSIGNED NOT NULL,
    target_channel VARCHAR(255) NOT NULL,
    publish_mode ENUM(
        'instant',
        'approval'
    ) NOT NULL,
    scheduled_for DATETIME NULL,
    published_at DATETIME NULL,
    publisher_payload_json JSON NULL,
    external_message_id VARCHAR(255) NULL,

    publication_status ENUM(
        'PENDING',
        'PUBLISHED',
        'FAILED',
        'SKIPPED'
    ) NOT NULL DEFAULT 'PENDING',

    error_message TEXT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_publications_task_id (task_id),
    KEY idx_publications_publication_status (publication_status),
    KEY idx_publications_published_at (published_at),
    CONSTRAINT fk_publications_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE user_actions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    upload_id BIGINT UNSIGNED NULL,
    batch_id BIGINT UNSIGNED NULL,
    task_id BIGINT UNSIGNED NULL,

    action_type ENUM(
        'LANGUAGE_SELECTED',
        'OPEN_INSTRUCTIONS',
        'UPLOAD_FILE',
        'REUPLOAD_FILE',
        'PUBLISH_CLICK',
        'DOWNLOAD_ARCHIVE_CLICK'
    ) NOT NULL,

    action_payload_json JSON NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    KEY idx_user_actions_user_id (user_id),
    KEY idx_user_actions_upload_id (upload_id),
    KEY idx_user_actions_batch_id (batch_id),
    KEY idx_user_actions_task_id (task_id),
    KEY idx_user_actions_action_type (action_type),
    CONSTRAINT fk_user_actions_user
        FOREIGN KEY (user_id) REFERENCES users(id)
        ON DELETE CASCADE
        ON UPDATE RESTRICT,
    CONSTRAINT fk_user_actions_upload
        FOREIGN KEY (upload_id) REFERENCES uploads(id)
        ON DELETE SET NULL
        ON UPDATE RESTRICT,
    CONSTRAINT fk_user_actions_batch
        FOREIGN KEY (batch_id) REFERENCES approval_batches(id)
        ON DELETE SET NULL
        ON UPDATE RESTRICT,
    CONSTRAINT fk_user_actions_task
        FOREIGN KEY (task_id) REFERENCES tasks(id)
        ON DELETE SET NULL
        ON UPDATE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO article_packages (package_code, articles_qty, price_amount, currency_code, is_active)
VALUES
    ('ARTICLES_14', 14, NULL, NULL, 1),
    ('ARTICLES_42', 42, NULL, NULL, 1),
    ('ARTICLES_84', 84, NULL, NULL, 1)
ON DUPLICATE KEY UPDATE
    articles_qty = VALUES(articles_qty),
    price_amount = VALUES(price_amount),
    currency_code = VALUES(currency_code),
    is_active = VALUES(is_active);
