-- Baseline schema: the live database schema as it was when migrations were introduced.
-- Generated with pg_dump -s. Do not edit; add changes as new revisions.
--
-- PostgreSQL database dump
--


-- Dumped from database version 16.15
-- Dumped by pg_dump version 16.15

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;

--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';

--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;

--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';

--
-- Name: approval_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.approval_status AS ENUM (
    'pending',
    'approved',
    'rejected',
    'cancelled'
);

--
-- Name: audit_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.audit_status AS ENUM (
    'success',
    'failure'
);

--
-- Name: auth_method; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.auth_method AS ENUM (
    'ssh_key',
    'ssh_password'
);

--
-- Name: auth_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.auth_type AS ENUM (
    'local',
    'ldap',
    'oauth'
);

--
-- Name: connectivity_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.connectivity_status AS ENUM (
    'healthy',
    'degraded',
    'unreachable',
    'unknown'
);

--
-- Name: execution_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.execution_status AS ENUM (
    'pending',
    'executing',
    'success',
    'failed',
    'rolled_back',
    'skipped'
);

--
-- Name: job_priority; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.job_priority AS ENUM (
    'low',
    'medium',
    'high',
    'critical'
);

--
-- Name: job_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.job_status AS ENUM (
    'pending',
    'running',
    'completed',
    'failed',
    'rolled_back',
    'cancelled'
);

--
-- Name: os_family; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.os_family AS ENUM (
    'debian',
    'rhel'
);

--
-- Name: os_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.os_type AS ENUM (
    'ubuntu',
    'debian',
    'centos',
    'redhat',
    'fedora',
    'rocky',
    'almalinux'
);

--
-- Name: patch_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.patch_type AS ENUM (
    'security',
    'bugfix',
    'enhancement',
    'critical'
);

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    actor_id uuid,
    action character varying(100) NOT NULL,
    resource_type character varying(100) NOT NULL,
    resource_id uuid,
    changes jsonb DEFAULT '{}'::jsonb,
    status public.audit_status DEFAULT 'success'::public.audit_status NOT NULL,
    reason text,
    ip_address inet,
    user_agent character varying(500),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: compliance_assessments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.compliance_assessments (
    id uuid NOT NULL,
    framework character varying(50) NOT NULL,
    status character varying(20) NOT NULL,
    results jsonb,
    error text,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    completed_at timestamp without time zone
);

--
-- Name: patch_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patch_groups (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    description text,
    patch_ids jsonb NOT NULL,
    server_ids jsonb NOT NULL,
    change_id character varying(100),
    change_management_system character varying(50),
    approval_status public.approval_status NOT NULL,
    approver_id uuid,
    approved_at timestamp without time zone,
    approval_notes text,
    business_justification text,
    risk_level character varying(50),
    maintenance_window character varying(255),
    is_active boolean NOT NULL,
    created_by_id uuid,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    scheduled_date timestamp without time zone,
    metadata_ jsonb
);

--
-- Name: patch_job_executions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patch_job_executions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    job_id uuid NOT NULL,
    server_id uuid NOT NULL,
    status public.execution_status DEFAULT 'pending'::public.execution_status NOT NULL,
    execution_start timestamp without time zone,
    execution_end timestamp without time zone,
    output text,
    error_message text,
    rollback_needed boolean DEFAULT false NOT NULL,
    rollback_performed boolean DEFAULT false NOT NULL,
    metadata_ jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: patch_job_patches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patch_job_patches (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    job_id uuid NOT NULL,
    patch_id uuid NOT NULL
);

--
-- Name: patch_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patch_jobs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(255) NOT NULL,
    status public.job_status DEFAULT 'pending'::public.job_status NOT NULL,
    created_by_id uuid,
    schedule_id uuid,
    start_time timestamp without time zone,
    end_time timestamp without time zone,
    patch_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    server_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    priority public.job_priority DEFAULT 'medium'::public.job_priority NOT NULL,
    dry_run boolean DEFAULT false NOT NULL,
    auto_rollback boolean DEFAULT false NOT NULL,
    metadata_ jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    patch_group_id uuid,
    change_id character varying(100),
    change_management_system character varying(50),
    approval_status public.approval_status,
    approver_id uuid,
    approval_notes text,
    approved_at timestamp without time zone,
    business_justification text,
    risk_level character varying(50),
    reboot_after boolean DEFAULT false,
    graceful_reboot boolean DEFAULT true,
    backup_before boolean DEFAULT false NOT NULL,
    restore_on_failure boolean DEFAULT false NOT NULL
);

--
-- Name: patches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.patches (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    package_name character varying(255) NOT NULL,
    version character varying(100) NOT NULL,
    os_family public.os_family NOT NULL,
    patch_type public.patch_type NOT NULL,
    release_date date,
    description text,
    cve_ids character varying(255)[],
    metadata_ jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.roles (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(255) NOT NULL,
    description character varying(1000),
    permissions jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schedules (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(255) NOT NULL,
    description character varying(1000),
    cron_expression character varying(100) NOT NULL,
    timezone character varying(100) DEFAULT 'UTC'::character varying NOT NULL,
    duration_minutes character varying NOT NULL,
    server_ids uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    patch_filters jsonb DEFAULT '{}'::jsonb,
    is_active boolean DEFAULT true NOT NULL,
    created_by_id uuid,
    metadata_ jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: server_patch_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.server_patch_status (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    server_id uuid NOT NULL,
    patch_id uuid NOT NULL,
    status character varying(50) NOT NULL,
    last_scan_at timestamp without time zone,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: servers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.servers (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    hostname character varying(255) NOT NULL,
    ip_address inet,
    os_type public.os_type NOT NULL,
    os_version character varying(100),
    auth_method public.auth_method NOT NULL,
    port character varying(10),
    credentials jsonb DEFAULT '{}'::jsonb NOT NULL,
    connectivity_status public.connectivity_status DEFAULT 'unknown'::public.connectivity_status NOT NULL,
    last_check_at timestamp without time zone,
    last_patch_scan_at timestamp without time zone,
    metadata_ jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: session_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.session_tokens (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    token_jti character varying(500) NOT NULL,
    expires_at timestamp without time zone NOT NULL,
    revoked_at timestamp without time zone,
    revoked_reason character varying(255),
    created_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: system_settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_settings (
    id uuid NOT NULL,
    patching_config jsonb NOT NULL,
    auto_approve_critical character varying(1) NOT NULL,
    require_approval_for_kernel character varying(1) NOT NULL,
    maintenance_window_start character varying(50),
    maintenance_window_end character varying(50),
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL,
    general_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    backup_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    tls_config jsonb DEFAULT '{}'::jsonb NOT NULL,
    notification_config jsonb DEFAULT '{}'::jsonb NOT NULL
);

--
-- Name: user_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_roles (
    user_id uuid NOT NULL,
    role_id uuid NOT NULL
);

--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    username character varying(255) NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255),
    auth_type public.auth_type DEFAULT 'local'::public.auth_type NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    metadata_ jsonb DEFAULT '{}'::jsonb,
    created_at timestamp without time zone DEFAULT now() NOT NULL,
    updated_at timestamp without time zone DEFAULT now() NOT NULL
);

--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);

--
-- Name: compliance_assessments compliance_assessments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.compliance_assessments
    ADD CONSTRAINT compliance_assessments_pkey PRIMARY KEY (id);

--
-- Name: patch_groups patch_groups_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_groups
    ADD CONSTRAINT patch_groups_name_key UNIQUE (name);

--
-- Name: patch_groups patch_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_groups
    ADD CONSTRAINT patch_groups_pkey PRIMARY KEY (id);

--
-- Name: patch_job_executions patch_job_executions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_job_executions
    ADD CONSTRAINT patch_job_executions_pkey PRIMARY KEY (id);

--
-- Name: patch_job_patches patch_job_patches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_job_patches
    ADD CONSTRAINT patch_job_patches_pkey PRIMARY KEY (id);

--
-- Name: patch_jobs patch_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_jobs
    ADD CONSTRAINT patch_jobs_pkey PRIMARY KEY (id);

--
-- Name: patches patches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patches
    ADD CONSTRAINT patches_pkey PRIMARY KEY (id);

--
-- Name: roles roles_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_name_key UNIQUE (name);

--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);

--
-- Name: schedules schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_pkey PRIMARY KEY (id);

--
-- Name: server_patch_status server_patch_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.server_patch_status
    ADD CONSTRAINT server_patch_status_pkey PRIMARY KEY (id);

--
-- Name: servers servers_hostname_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.servers
    ADD CONSTRAINT servers_hostname_key UNIQUE (hostname);

--
-- Name: servers servers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.servers
    ADD CONSTRAINT servers_pkey PRIMARY KEY (id);

--
-- Name: session_tokens session_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_tokens
    ADD CONSTRAINT session_tokens_pkey PRIMARY KEY (id);

--
-- Name: session_tokens session_tokens_token_jti_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_tokens
    ADD CONSTRAINT session_tokens_token_jti_key UNIQUE (token_jti);

--
-- Name: system_settings system_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_settings
    ADD CONSTRAINT system_settings_pkey PRIMARY KEY (id);

--
-- Name: user_roles user_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role_id);

--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);

--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);

--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);

--
-- Name: idx_audit_action; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_action ON public.audit_logs USING btree (action);

--
-- Name: idx_audit_actor; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_actor ON public.audit_logs USING btree (actor_id);

--
-- Name: idx_audit_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_created ON public.audit_logs USING btree (created_at);

--
-- Name: idx_audit_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_resource ON public.audit_logs USING btree (resource_type, resource_id);

--
-- Name: idx_audit_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_audit_status ON public.audit_logs USING btree (status);

--
-- Name: idx_executions_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_executions_job ON public.patch_job_executions USING btree (job_id);

--
-- Name: idx_executions_server; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_executions_server ON public.patch_job_executions USING btree (server_id);

--
-- Name: idx_executions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_executions_status ON public.patch_job_executions USING btree (status);

--
-- Name: idx_job_patches_job; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_job_patches_job ON public.patch_job_patches USING btree (job_id);

--
-- Name: idx_jobs_created_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_created_by ON public.patch_jobs USING btree (created_by_id);

--
-- Name: idx_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_jobs_status ON public.patch_jobs USING btree (status);

--
-- Name: idx_patch_status_server; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patch_status_server ON public.server_patch_status USING btree (server_id);

--
-- Name: idx_patch_status_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patch_status_status ON public.server_patch_status USING btree (status);

--
-- Name: idx_patches_os_family; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patches_os_family ON public.patches USING btree (os_family);

--
-- Name: idx_patches_package; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patches_package ON public.patches USING btree (package_name);

--
-- Name: idx_patches_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patches_type ON public.patches USING btree (patch_type);

--
-- Name: idx_patches_version; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_patches_version ON public.patches USING btree (version);

--
-- Name: idx_roles_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_roles_name ON public.roles USING btree (name);

--
-- Name: idx_servers_auth_method; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_servers_auth_method ON public.servers USING btree (auth_method);

--
-- Name: idx_servers_connectivity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_servers_connectivity ON public.servers USING btree (connectivity_status);

--
-- Name: idx_servers_hostname; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_servers_hostname ON public.servers USING btree (hostname);

--
-- Name: idx_servers_os_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_servers_os_type ON public.servers USING btree (os_type);

--
-- Name: idx_session_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_session_expires ON public.session_tokens USING btree (expires_at);

--
-- Name: idx_session_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_session_user ON public.session_tokens USING btree (user_id);

--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_email ON public.users USING btree (email);

--
-- Name: idx_users_username; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_username ON public.users USING btree (username);

--
-- Name: ix_compliance_assessments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_compliance_assessments_status ON public.compliance_assessments USING btree (status);

--
-- Name: audit_logs audit_logs_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id);

--
-- Name: patch_groups patch_groups_approver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_groups
    ADD CONSTRAINT patch_groups_approver_id_fkey FOREIGN KEY (approver_id) REFERENCES public.users(id);

--
-- Name: patch_groups patch_groups_created_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_groups
    ADD CONSTRAINT patch_groups_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.users(id);

--
-- Name: patch_job_executions patch_job_executions_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_job_executions
    ADD CONSTRAINT patch_job_executions_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.patch_jobs(id) ON DELETE CASCADE;

--
-- Name: patch_job_executions patch_job_executions_server_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_job_executions
    ADD CONSTRAINT patch_job_executions_server_id_fkey FOREIGN KEY (server_id) REFERENCES public.servers(id);

--
-- Name: patch_job_patches patch_job_patches_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_job_patches
    ADD CONSTRAINT patch_job_patches_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.patch_jobs(id) ON DELETE CASCADE;

--
-- Name: patch_job_patches patch_job_patches_patch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_job_patches
    ADD CONSTRAINT patch_job_patches_patch_id_fkey FOREIGN KEY (patch_id) REFERENCES public.patches(id);

--
-- Name: patch_jobs patch_jobs_approver_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_jobs
    ADD CONSTRAINT patch_jobs_approver_id_fkey FOREIGN KEY (approver_id) REFERENCES public.users(id);

--
-- Name: patch_jobs patch_jobs_created_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_jobs
    ADD CONSTRAINT patch_jobs_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.users(id);

--
-- Name: patch_jobs patch_jobs_patch_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_jobs
    ADD CONSTRAINT patch_jobs_patch_group_id_fkey FOREIGN KEY (patch_group_id) REFERENCES public.patch_groups(id);

--
-- Name: patch_jobs patch_jobs_schedule_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.patch_jobs
    ADD CONSTRAINT patch_jobs_schedule_id_fkey FOREIGN KEY (schedule_id) REFERENCES public.schedules(id);

--
-- Name: schedules schedules_created_by_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schedules
    ADD CONSTRAINT schedules_created_by_id_fkey FOREIGN KEY (created_by_id) REFERENCES public.users(id);

--
-- Name: session_tokens session_tokens_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_tokens
    ADD CONSTRAINT session_tokens_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

--
-- Name: user_roles user_roles_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE CASCADE;

--
-- Name: user_roles user_roles_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_roles
    ADD CONSTRAINT user_roles_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;

--
-- PostgreSQL database dump complete
--


