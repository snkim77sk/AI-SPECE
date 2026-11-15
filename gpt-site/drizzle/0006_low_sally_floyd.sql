CREATE TABLE `budget_receipts` (
	`session_id` text NOT NULL,
	`page` integer NOT NULL,
	`rows` integer NOT NULL,
	`bytes` integer NOT NULL,
	`hash` text NOT NULL,
	`committed_at` text NOT NULL,
	PRIMARY KEY(`session_id`, `page`)
);
--> statement-breakpoint
CREATE TABLE `budget_revisions` (
	`mode` text NOT NULL,
	`service` text NOT NULL,
	`source_key` text NOT NULL,
	`payload_hash` text NOT NULL,
	`row_json` text NOT NULL,
	`first_seen` text NOT NULL,
	`first_snapshot` text NOT NULL,
	PRIMARY KEY(`mode`, `service`, `source_key`, `payload_hash`)
);
--> statement-breakpoint
CREATE TABLE `budget_rows` (
	`mode` text NOT NULL,
	`service` text NOT NULL,
	`source_key` text NOT NULL,
	`payload_hash` text NOT NULL,
	`fiscal_year` integer NOT NULL,
	`snapshot_date` text NOT NULL,
	`category` text NOT NULL,
	`region` text NOT NULL,
	`org` text NOT NULL,
	`dept` text NOT NULL,
	`project` text NOT NULL,
	`row_json` text NOT NULL,
	`is_active` integer NOT NULL,
	`last_session` text NOT NULL,
	`updated_at` text NOT NULL,
	PRIMARY KEY(`mode`, `service`, `source_key`)
);
--> statement-breakpoint
CREATE INDEX `idx_budget_scope` ON `budget_rows` (`mode`,`service`,`fiscal_year`,`is_active`,`category`);--> statement-breakpoint
CREATE INDEX `idx_budget_region` ON `budget_rows` (`mode`,`region`,`org`);--> statement-breakpoint
CREATE TABLE `budget_seen` (
	`session_id` text NOT NULL,
	`source_key` text NOT NULL,
	PRIMARY KEY(`session_id`, `source_key`)
);
--> statement-breakpoint
CREATE TABLE `budget_sessions` (
	`id` text PRIMARY KEY NOT NULL,
	`mode` text NOT NULL,
	`service` text NOT NULL,
	`fiscal_year` integer NOT NULL,
	`snapshot_date` text NOT NULL,
	`history_only` integer NOT NULL,
	`state` text NOT NULL,
	`page` integer NOT NULL,
	`page_size` integer NOT NULL,
	`source_total` integer,
	`fetched` integer NOT NULL,
	`saw_full` integer NOT NULL,
	`fixture_total` integer NOT NULL,
	`max_payload` integer NOT NULL,
	`lease` text,
	`lease_until` integer NOT NULL,
	`error` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `source_usage` (
	`source` text NOT NULL,
	`day` text NOT NULL,
	`calls` integer NOT NULL,
	PRIMARY KEY(`source`, `day`)
);
