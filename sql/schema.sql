-- =====================================================================
-- WorkforceIQ -- relational schema for Everline Corp HR analytics
-- Target: PostgreSQL 15+
--
-- Design notes
--   * employee_id is a NATURAL key carried over from the source HRIS
--     extract (IBM EmployeeNumber), not a surrogate serial. Keeping it
--     stable means the ML scoring job and the BI layer can be re-run
--     against a refreshed extract without remapping identities.
--   * compensation_history and performance_reviews are effective-dated
--     (SCD Type 2 style): one row per employee per review cycle rather
--     than one mutable row per employee. This is what makes point-in-time
--     questions ("what was this person paid when they resigned?")
--     answerable at all.
--   * employees.manager_id is a self-referencing FK -- the hierarchy lives
--     in the same table, which is what vw_manager_span_attrition walks.
--   * dim_date exists because Power BI time-intelligence DAX
--     (SAMEPERIODLASTYEAR, DATESINPERIOD) requires a contiguous, marked
--     date table. Deriving it from fact dates alone leaves gaps in any
--     period with no terminations.
-- =====================================================================

BEGIN;

DROP TABLE IF EXISTS attrition_risk_scores CASCADE;
DROP TABLE IF EXISTS attrition_events      CASCADE;
DROP TABLE IF EXISTS performance_reviews   CASCADE;
DROP TABLE IF EXISTS compensation_history  CASCADE;
DROP TABLE IF EXISTS employees             CASCADE;
DROP TABLE IF EXISTS departments           CASCADE;
DROP TABLE IF EXISTS dim_date              CASCADE;


-- ---------------------------------------------------------------------
-- departments
-- ---------------------------------------------------------------------
CREATE TABLE departments (
    department_id    INTEGER      PRIMARY KEY,
    department_name  VARCHAR(60)  NOT NULL UNIQUE,
    division         VARCHAR(60)  NOT NULL
);

COMMENT ON TABLE departments IS
    'Everline Corp org units. division rolls departments up to the exec level.';


-- ---------------------------------------------------------------------
-- employees
-- ---------------------------------------------------------------------
CREATE TABLE employees (
    employee_id         INTEGER      PRIMARY KEY,
    first_name          VARCHAR(50)  NOT NULL,
    last_name           VARCHAR(50)  NOT NULL,
    department_id       INTEGER      NOT NULL,
    job_role            VARCHAR(60)  NOT NULL,
    job_level           SMALLINT     NOT NULL,
    hire_date           DATE         NOT NULL,
    gender              VARCHAR(10)  NOT NULL,
    age                 SMALLINT     NOT NULL,
    marital_status      VARCHAR(20)  NOT NULL,
    education_level     VARCHAR(20)  NOT NULL,
    distance_from_home  SMALLINT     NOT NULL,
    business_travel     VARCHAR(30)  NOT NULL,
    manager_id          INTEGER,
    current_status      VARCHAR(12)  NOT NULL,

    CONSTRAINT fk_employees_department
        FOREIGN KEY (department_id) REFERENCES departments (department_id),
    -- self-referencing: a manager is just another employee
    CONSTRAINT fk_employees_manager
        FOREIGN KEY (manager_id)    REFERENCES employees (employee_id),

    CONSTRAINT ck_employees_status
        CHECK (current_status IN ('Active', 'Terminated')),
    CONSTRAINT ck_employees_age
        CHECK (age BETWEEN 16 AND 90),
    CONSTRAINT ck_employees_job_level
        CHECK (job_level BETWEEN 1 AND 5),
    CONSTRAINT ck_employees_distance
        CHECK (distance_from_home >= 0),
    -- nobody manages themselves
    CONSTRAINT ck_employees_not_self_managed
        CHECK (manager_id IS NULL OR manager_id <> employee_id)
);

CREATE INDEX idx_employees_department ON employees (department_id);
CREATE INDEX idx_employees_manager    ON employees (manager_id);
CREATE INDEX idx_employees_status     ON employees (current_status);
CREATE INDEX idx_employees_hire_date  ON employees (hire_date);

COMMENT ON COLUMN employees.manager_id IS
    'Self-FK. NULL only for the six department heads at the top of each tree.';


-- ---------------------------------------------------------------------
-- compensation_history  (SCD Type 2 pattern)
-- ---------------------------------------------------------------------
CREATE TABLE compensation_history (
    comp_id             INTEGER       PRIMARY KEY,
    employee_id         INTEGER       NOT NULL,
    effective_date      DATE          NOT NULL,
    monthly_income      INTEGER       NOT NULL,
    salary_hike_pct     NUMERIC(4,1)  NOT NULL,
    stock_option_level  SMALLINT      NOT NULL,

    CONSTRAINT fk_comp_employee
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE,
    -- one compensation record per employee per effective date
    CONSTRAINT uq_comp_employee_date UNIQUE (employee_id, effective_date),

    CONSTRAINT ck_comp_income CHECK (monthly_income > 0),
    CONSTRAINT ck_comp_hike   CHECK (salary_hike_pct >= 0),
    CONSTRAINT ck_comp_stock  CHECK (stock_option_level BETWEEN 0 AND 3)
);

CREATE INDEX idx_comp_employee       ON compensation_history (employee_id);
CREATE INDEX idx_comp_effective_date ON compensation_history (effective_date);
-- covering index for the "latest row per employee" lookup the views do constantly
CREATE INDEX idx_comp_employee_date_desc
    ON compensation_history (employee_id, effective_date DESC);

COMMENT ON TABLE compensation_history IS
    'Effective-dated salary history. The current salary is the row with the '
    'greatest effective_date; historical rows are never updated in place.';


-- ---------------------------------------------------------------------
-- performance_reviews
-- ---------------------------------------------------------------------
CREATE TABLE performance_reviews (
    review_id                 INTEGER      PRIMARY KEY,
    employee_id               INTEGER      NOT NULL,
    review_date               DATE         NOT NULL,
    performance_rating        SMALLINT     NOT NULL,
    job_satisfaction          SMALLINT     NOT NULL,
    environment_satisfaction  SMALLINT     NOT NULL,
    work_life_balance         SMALLINT     NOT NULL,
    overtime_flag             VARCHAR(3)   NOT NULL,
    manager_id                INTEGER,

    CONSTRAINT fk_review_employee
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE,
    CONSTRAINT fk_review_manager
        FOREIGN KEY (manager_id)  REFERENCES employees (employee_id),
    CONSTRAINT uq_review_employee_date UNIQUE (employee_id, review_date),

    CONSTRAINT ck_review_perf     CHECK (performance_rating       BETWEEN 1 AND 5),
    CONSTRAINT ck_review_jobsat   CHECK (job_satisfaction         BETWEEN 1 AND 4),
    CONSTRAINT ck_review_envsat   CHECK (environment_satisfaction BETWEEN 1 AND 4),
    CONSTRAINT ck_review_wlb      CHECK (work_life_balance        BETWEEN 1 AND 4),
    CONSTRAINT ck_review_overtime CHECK (overtime_flag IN ('Yes', 'No'))
);

CREATE INDEX idx_review_employee ON performance_reviews (employee_id);
CREATE INDEX idx_review_date     ON performance_reviews (review_date);
CREATE INDEX idx_review_manager  ON performance_reviews (manager_id);
CREATE INDEX idx_review_employee_date_desc
    ON performance_reviews (employee_id, review_date DESC);

COMMENT ON COLUMN performance_reviews.manager_id IS
    'The manager of record AT REVIEW TIME. Denormalised on purpose: it lets '
    'manager-level attrition be attributed to who actually ran the team then, '
    'even after the employee or the org has since moved.';


-- ---------------------------------------------------------------------
-- attrition_events
-- ---------------------------------------------------------------------
CREATE TABLE attrition_events (
    event_id          INTEGER  PRIMARY KEY,
    employee_id       INTEGER  NOT NULL,
    termination_date  DATE     NOT NULL,
    attrition_flag    SMALLINT NOT NULL,
    voluntary_flag    SMALLINT NOT NULL,

    CONSTRAINT fk_attrition_employee
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE,
    -- an employee leaves Everline at most once in this dataset
    CONSTRAINT uq_attrition_employee UNIQUE (employee_id),

    CONSTRAINT ck_attrition_flag   CHECK (attrition_flag IN (0, 1)),
    CONSTRAINT ck_attrition_vol    CHECK (voluntary_flag IN (0, 1))
);

CREATE INDEX idx_attrition_employee ON attrition_events (employee_id);
CREATE INDEX idx_attrition_date     ON attrition_events (termination_date);


-- ---------------------------------------------------------------------
-- attrition_risk_scores  (written by notebooks/attrition_risk_model.ipynb)
-- ---------------------------------------------------------------------
CREATE TABLE attrition_risk_scores (
    employee_id  INTEGER       NOT NULL,
    scored_date  DATE          NOT NULL,
    risk_score   NUMERIC(6,5)  NOT NULL,
    risk_tier    VARCHAR(10)   NOT NULL,
    model_name   VARCHAR(40)   NOT NULL,

    -- composite PK: keeping every scoring run means model drift is
    -- observable over time instead of being overwritten each night
    CONSTRAINT pk_risk PRIMARY KEY (employee_id, scored_date, model_name),
    CONSTRAINT fk_risk_employee
        FOREIGN KEY (employee_id) REFERENCES employees (employee_id) ON DELETE CASCADE,

    CONSTRAINT ck_risk_score CHECK (risk_score BETWEEN 0 AND 1),
    CONSTRAINT ck_risk_tier  CHECK (risk_tier IN ('Low', 'Medium', 'High'))
);

CREATE INDEX idx_risk_score ON attrition_risk_scores (risk_score DESC);
CREATE INDEX idx_risk_date  ON attrition_risk_scores (scored_date);


-- ---------------------------------------------------------------------
-- dim_date
-- ---------------------------------------------------------------------
CREATE TABLE dim_date (
    date_key      DATE         PRIMARY KEY,
    year          SMALLINT     NOT NULL,
    quarter       SMALLINT     NOT NULL,
    month         SMALLINT     NOT NULL,
    month_name    VARCHAR(12)  NOT NULL,
    year_quarter  VARCHAR(8)   NOT NULL,
    year_month    VARCHAR(8)   NOT NULL,
    is_month_end  BOOLEAN      NOT NULL
);

CREATE INDEX idx_dim_date_year_quarter ON dim_date (year, quarter);

COMMENT ON TABLE dim_date IS
    'Contiguous 2015-2026 calendar. Mark this as the date table in Power BI '
    'so SAMEPERIODLASTYEAR / DATESINPERIOD behave correctly.';

COMMIT;
