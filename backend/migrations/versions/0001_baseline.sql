-- Baseline schema (v0.4), frozen from the models when migrations were introduced.
-- Never edit this file: later changes go in new migrations (alembic revision --autogenerate).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS restricted;

CREATE TYPE agent_run_status AS ENUM ('planning', 'awaiting_approval', 'running', 'completed', 'cancelled', 'failed');

CREATE TYPE shape_type AS ENUM ('bbox', 'polygon', 'mask', 'point', 'polyline', 'classification');

CREATE TYPE annotation_source AS ENUM ('human', 'tool_assisted', 'import');

CREATE TYPE import_status AS ENUM ('uploaded', 'inspecting', 'awaiting_approval', 'committing', 'committed', 'rolled_back', 'failed', 'cancelled');

CREATE TYPE user_role AS ENUM ('admin', 'annotator', 'reviewer', 'operator', 'viewer');

CREATE TYPE item_status AS ENUM ('new', 'in_progress', 'annotated', 'in_review', 'done', 'rejected');

CREATE TYPE dataset_split AS ENUM ('train', 'val', 'test');

CREATE TYPE task_stage AS ENUM ('annotate', 'review', 'done');

CREATE TYPE review_verdict AS ENUM ('approved', 'rejected');

CREATE TABLE users (
    email VARCHAR(320) NOT NULL, 
    display_name VARCHAR(200) NOT NULL, 
    role user_role NOT NULL, 
    is_active BOOLEAN NOT NULL, 
    password_hash VARCHAR(256), 
    last_login_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_users PRIMARY KEY (id), 
    CONSTRAINT uq_users_email UNIQUE (email)
);

CREATE TABLE projects (
    name VARCHAR(200) NOT NULL, 
    description TEXT, 
    domain_pack VARCHAR(50) NOT NULL, 
    settings JSONB NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_projects PRIMARY KEY (id), 
    CONSTRAINT uq_projects_name UNIQUE (name)
);

CREATE TABLE agent_runs (
    project_id UUID, 
    user_id UUID, 
    kind VARCHAR(50) NOT NULL, 
    request TEXT NOT NULL, 
    plan JSONB NOT NULL, 
    status agent_run_status NOT NULL, 
    approved_by UUID, 
    approved_at TIMESTAMP WITH TIME ZONE, 
    tool_calls JSONB NOT NULL, 
    result JSONB NOT NULL, 
    llm_provider VARCHAR(50), 
    llm_model VARCHAR(100), 
    error TEXT, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_agent_runs PRIMARY KEY (id), 
    CONSTRAINT fk_agent_runs_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_agent_runs_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, 
    CONSTRAINT fk_agent_runs_approved_by_users FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_agent_runs_project_id ON agent_runs (project_id);

CREATE INDEX ix_agent_runs_user_id ON agent_runs (user_id);

CREATE INDEX ix_agent_runs_kind ON agent_runs (kind);

CREATE TABLE auth_sessions (
    token_hash VARCHAR(64) NOT NULL, 
    user_id UUID NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    last_seen_at TIMESTAMP WITH TIME ZONE, 
    user_agent VARCHAR(300), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_auth_sessions PRIMARY KEY (token_hash), 
    CONSTRAINT fk_auth_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_auth_sessions_user_id ON auth_sessions (user_id);

CREATE INDEX ix_auth_sessions_expires_at ON auth_sessions (expires_at);

CREATE TABLE import_batches (
    project_id UUID NOT NULL, 
    uploaded_by UUID, 
    detected_format VARCHAR(50), 
    original_key VARCHAR(1024) NOT NULL, 
    status import_status NOT NULL, 
    label_mapping JSONB NOT NULL, 
    conflict_policy VARCHAR(20), 
    report JSONB NOT NULL, 
    error TEXT, 
    approved_by UUID, 
    committed_at TIMESTAMP WITH TIME ZONE, 
    rolled_back_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_import_batches PRIMARY KEY (id), 
    CONSTRAINT fk_import_batches_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_import_batches_uploaded_by_users FOREIGN KEY(uploaded_by) REFERENCES users (id) ON DELETE SET NULL, 
    CONSTRAINT fk_import_batches_approved_by_users FOREIGN KEY(approved_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_import_batches_project_id ON import_batches (project_id);

CREATE TABLE hierarchy_levels (
    project_id UUID NOT NULL, 
    depth INTEGER NOT NULL, 
    name VARCHAR(100) NOT NULL, 
    metadata_schema JSONB NOT NULL, 
    is_split_level BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    CONSTRAINT pk_hierarchy_levels PRIMARY KEY (id), 
    CONSTRAINT uq_hierarchy_levels_project_id_depth UNIQUE (project_id, depth), 
    CONSTRAINT uq_hierarchy_levels_project_id_name UNIQUE (project_id, name), 
    CONSTRAINT fk_hierarchy_levels_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE
);

CREATE INDEX ix_hierarchy_levels_project_id ON hierarchy_levels (project_id);

CREATE TABLE label_classes (
    project_id UUID NOT NULL, 
    name VARCHAR(100) NOT NULL, 
    color VARCHAR(9) NOT NULL, 
    allowed_shapes VARCHAR(20)[] NOT NULL, 
    attributes_schema JSONB NOT NULL, 
    must_be_inside_id UUID, 
    is_archived BOOLEAN NOT NULL, 
    hotkey VARCHAR(4), 
    sort_order INTEGER NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_label_classes PRIMARY KEY (id), 
    CONSTRAINT uq_label_classes_project_id_name UNIQUE (project_id, name), 
    CONSTRAINT fk_label_classes_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_label_classes_must_be_inside_id_label_classes FOREIGN KEY(must_be_inside_id) REFERENCES label_classes (id) ON DELETE SET NULL
);

CREATE INDEX ix_label_classes_project_id ON label_classes (project_id);

CREATE TABLE documents (
    project_id UUID NOT NULL, 
    title VARCHAR(300) NOT NULL, 
    file_name VARCHAR(300) NOT NULL, 
    storage_key VARCHAR(1024) NOT NULL, 
    uploaded_by UUID, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_documents PRIMARY KEY (id), 
    CONSTRAINT fk_documents_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_documents_uploaded_by_users FOREIGN KEY(uploaded_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_documents_project_id ON documents (project_id);

CREATE TABLE user_sessions (
    user_id UUID NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    ended_at TIMESTAMP WITH TIME ZONE, 
    client VARCHAR(200), 
    id UUID NOT NULL, 
    CONSTRAINT pk_user_sessions PRIMARY KEY (id), 
    CONSTRAINT fk_user_sessions_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);

CREATE TABLE nodes (
    project_id UUID NOT NULL, 
    level_id UUID NOT NULL, 
    parent_id UUID, 
    external_id VARCHAR(200) NOT NULL, 
    metadata JSONB NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_nodes PRIMARY KEY (id), 
    CONSTRAINT uq_nodes_project_id_level_id_external_id UNIQUE (project_id, level_id, external_id), 
    CONSTRAINT fk_nodes_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_nodes_level_id_hierarchy_levels FOREIGN KEY(level_id) REFERENCES hierarchy_levels (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_nodes_parent_id_nodes FOREIGN KEY(parent_id) REFERENCES nodes (id) ON DELETE CASCADE
);

CREATE INDEX ix_nodes_project_id ON nodes (project_id);

CREATE INDEX ix_nodes_level_id ON nodes (level_id);

CREATE INDEX ix_nodes_parent_id ON nodes (parent_id);

CREATE TABLE document_chunks (
    document_id UUID NOT NULL, 
    position INTEGER NOT NULL, 
    section VARCHAR(500) NOT NULL, 
    page INTEGER, 
    text TEXT NOT NULL, 
    tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', coalesce(section, '') || ' ' || text)) STORED NOT NULL, 
    id UUID NOT NULL, 
    CONSTRAINT pk_document_chunks PRIMARY KEY (id), 
    CONSTRAINT fk_document_chunks_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE CASCADE
);

CREATE INDEX ix_document_chunks_document_id ON document_chunks (document_id);

CREATE INDEX ix_document_chunks_tsv ON document_chunks USING gin (tsv);

CREATE TABLE datasets (
    project_id UUID NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    spec JSONB NOT NULL, 
    split_level_id UUID, 
    export_format VARCHAR(50), 
    manifest_key VARCHAR(1024), 
    export_key VARCHAR(1024), 
    created_by UUID, 
    agent_run_id UUID, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_datasets PRIMARY KEY (id), 
    CONSTRAINT fk_datasets_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_datasets_split_level_id_hierarchy_levels FOREIGN KEY(split_level_id) REFERENCES hierarchy_levels (id) ON DELETE SET NULL, 
    CONSTRAINT fk_datasets_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
    CONSTRAINT fk_datasets_agent_run_id_agent_runs FOREIGN KEY(agent_run_id) REFERENCES agent_runs (id) ON DELETE SET NULL
);

CREATE INDEX ix_datasets_project_id ON datasets (project_id);

CREATE TABLE items (
    project_id UUID NOT NULL, 
    node_id UUID, 
    storage_key VARCHAR(1024) NOT NULL, 
    original_filename VARCHAR(1024) NOT NULL, 
    sha256 VARCHAR(64) NOT NULL, 
    phash VARCHAR(32), 
    width INTEGER NOT NULL, 
    height INTEGER NOT NULL, 
    size_bytes BIGINT NOT NULL, 
    is_tiled BOOLEAN NOT NULL, 
    tiles_prefix VARCHAR(1024), 
    tile_size INTEGER, 
    tile_format VARCHAR(8), 
    status item_status NOT NULL, 
    assignee_id UUID, 
    thumb_key VARCHAR(1024), 
    metadata JSONB NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_items PRIMARY KEY (id), 
    CONSTRAINT uq_items_project_id_sha256 UNIQUE (project_id, sha256), 
    CONSTRAINT fk_items_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_items_node_id_nodes FOREIGN KEY(node_id) REFERENCES nodes (id) ON DELETE SET NULL, 
    CONSTRAINT fk_items_assignee_id_users FOREIGN KEY(assignee_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_items_phash ON items (phash);

CREATE INDEX ix_items_status ON items (status);

CREATE INDEX ix_items_assignee_id ON items (assignee_id);

CREATE INDEX ix_items_project_id ON items (project_id);

CREATE INDEX ix_items_node_id ON items (node_id);

CREATE TABLE restricted.identity_mappings (
    node_id UUID NOT NULL, 
    real_identifier VARCHAR(500) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_identity_mappings PRIMARY KEY (node_id), 
    CONSTRAINT fk_identity_mappings_node_id_nodes FOREIGN KEY(node_id) REFERENCES nodes (id) ON DELETE CASCADE
);

CREATE TABLE annotations (
    annotation_id UUID DEFAULT gen_random_uuid() NOT NULL, 
    version INTEGER NOT NULL, 
    item_id UUID NOT NULL, 
    label_class_id UUID NOT NULL, 
    shape_type shape_type NOT NULL, 
    geometry geometry(GEOMETRY,0), 
    attributes JSONB NOT NULL, 
    is_deleted BOOLEAN NOT NULL, 
    source annotation_source NOT NULL, 
    import_batch_id UUID, 
    created_by UUID, 
    reviewed BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_annotations PRIMARY KEY (id), 
    CONSTRAINT uq_annotations_annotation_id_version UNIQUE (annotation_id, version), 
    CONSTRAINT fk_annotations_item_id_items FOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE, 
    CONSTRAINT fk_annotations_label_class_id_label_classes FOREIGN KEY(label_class_id) REFERENCES label_classes (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_annotations_import_batch_id_import_batches FOREIGN KEY(import_batch_id) REFERENCES import_batches (id) ON DELETE SET NULL, 
    CONSTRAINT fk_annotations_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_annotations_import_batch_id ON annotations (import_batch_id);

CREATE INDEX ix_annotations_annotation_id ON annotations (annotation_id);

CREATE INDEX ix_annotations_created_by ON annotations (created_by);

CREATE INDEX ix_annotations_label_class_id ON annotations (label_class_id);

CREATE INDEX ix_annotations_geometry ON annotations USING gist (geometry);

CREATE INDEX ix_annotations_item_current ON annotations (item_id, annotation_id, version);

CREATE TABLE dataset_items (
    dataset_id UUID NOT NULL, 
    item_id UUID NOT NULL, 
    split dataset_split NOT NULL, 
    CONSTRAINT pk_dataset_items PRIMARY KEY (dataset_id, item_id), 
    CONSTRAINT fk_dataset_items_dataset_id_datasets FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE, 
    CONSTRAINT fk_dataset_items_item_id_items FOREIGN KEY(item_id) REFERENCES items (id) ON DELETE RESTRICT
);

CREATE TABLE tasks (
    project_id UUID NOT NULL, 
    node_id UUID, 
    item_id UUID, 
    assignee_id UUID, 
    stage task_stage NOT NULL, 
    due_at TIMESTAMP WITH TIME ZONE, 
    created_by UUID, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_tasks PRIMARY KEY (id), 
    CONSTRAINT fk_tasks_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_tasks_node_id_nodes FOREIGN KEY(node_id) REFERENCES nodes (id) ON DELETE CASCADE, 
    CONSTRAINT fk_tasks_item_id_items FOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE, 
    CONSTRAINT fk_tasks_assignee_id_users FOREIGN KEY(assignee_id) REFERENCES users (id) ON DELETE SET NULL, 
    CONSTRAINT fk_tasks_created_by_users FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_tasks_node_id ON tasks (node_id);

CREATE INDEX ix_tasks_project_id ON tasks (project_id);

CREATE INDEX ix_tasks_assignee_id ON tasks (assignee_id);

CREATE INDEX ix_tasks_item_id ON tasks (item_id);

CREATE INDEX ix_tasks_stage ON tasks (stage);

CREATE TABLE events (
    id BIGSERIAL NOT NULL, 
    project_id UUID, 
    user_id UUID, 
    session_id UUID, 
    item_id UUID, 
    action VARCHAR(100) NOT NULL, 
    active_ms INTEGER NOT NULL, 
    payload JSONB NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_events PRIMARY KEY (id), 
    CONSTRAINT fk_events_project_id_projects FOREIGN KEY(project_id) REFERENCES projects (id) ON DELETE CASCADE, 
    CONSTRAINT fk_events_user_id_users FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, 
    CONSTRAINT fk_events_session_id_user_sessions FOREIGN KEY(session_id) REFERENCES user_sessions (id) ON DELETE SET NULL, 
    CONSTRAINT fk_events_item_id_items FOREIGN KEY(item_id) REFERENCES items (id) ON DELETE SET NULL
);

CREATE INDEX ix_events_user_created ON events (user_id, created_at);

CREATE INDEX ix_events_action ON events (action);

CREATE INDEX ix_events_item_id ON events (item_id);

CREATE INDEX ix_events_project_created ON events (project_id, created_at);

CREATE TABLE dataset_annotations (
    dataset_id UUID NOT NULL, 
    annotation_row_id UUID NOT NULL, 
    CONSTRAINT pk_dataset_annotations PRIMARY KEY (dataset_id, annotation_row_id), 
    CONSTRAINT fk_dataset_annotations_dataset_id_datasets FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE, 
    CONSTRAINT fk_dataset_annotations_annotation_row_id_annotations FOREIGN KEY(annotation_row_id) REFERENCES annotations (id) ON DELETE RESTRICT
);

CREATE TABLE reviews (
    item_id UUID NOT NULL, 
    task_id UUID, 
    reviewer_id UUID, 
    verdict review_verdict NOT NULL, 
    comment TEXT, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_reviews PRIMARY KEY (id), 
    CONSTRAINT fk_reviews_item_id_items FOREIGN KEY(item_id) REFERENCES items (id) ON DELETE CASCADE, 
    CONSTRAINT fk_reviews_task_id_tasks FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE SET NULL, 
    CONSTRAINT fk_reviews_reviewer_id_users FOREIGN KEY(reviewer_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_reviews_reviewer_id ON reviews (reviewer_id);

CREATE INDEX ix_reviews_item_id ON reviews (item_id);

CREATE INDEX ix_reviews_task_id ON reviews (task_id);
