CREATE TABLE `institution_pattern_events` (
	`run_id` text NOT NULL,
	`kind` text NOT NULL,
	`source_event_key` text NOT NULL,
	`region_key` text NOT NULL,
	`org_key` text NOT NULL,
	`category` text NOT NULL,
	`fiscal_year` integer NOT NULL,
	`budget_key` text NOT NULL,
	`kind_order` integer NOT NULL,
	`row_json` text NOT NULL,
	`eligible` integer NOT NULL,
	PRIMARY KEY(`run_id`, `kind`, `source_event_key`),
	FOREIGN KEY (`run_id`) REFERENCES `institution_pattern_runs`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `idx_institution_event_page` ON `institution_pattern_events` (`run_id`,`eligible`,`region_key`,`org_key`,`category`,`fiscal_year`,`budget_key`,`kind_order`,`source_event_key`);--> statement-breakpoint
CREATE TABLE `institution_pattern_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`request_id` text NOT NULL,
	`mode` text NOT NULL,
	`matching_job_id` text NOT NULL,
	`matching_engine_version` text NOT NULL,
	`pattern_version` text NOT NULL,
	`projection_version` text NOT NULL,
	`g2b_epoch` integer NOT NULL,
	`budget_epoch` integer NOT NULL,
	`from_day` text NOT NULL,
	`to_day` text NOT NULL,
	`budget_scope_from` text NOT NULL,
	`budget_scope_to` text NOT NULL,
	`scope_json` text NOT NULL,
	`scope_hash` text NOT NULL,
	`state` text NOT NULL,
	`phase` text NOT NULL,
	`cursor_json` text NOT NULL,
	`partial_json` text,
	`generation` integer NOT NULL,
	`min_score` integer NOT NULL,
	`indexed_projects` integer NOT NULL,
	`indexed_requests` integer NOT NULL,
	`excluded_projects` integer NOT NULL,
	`excluded_requests` integer NOT NULL,
	`processed_projects` integer NOT NULL,
	`processed_requests` integer NOT NULL,
	`completed_groups` integer NOT NULL,
	`lease` text,
	`lease_until` integer NOT NULL,
	`error` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`completed_at` text,
	FOREIGN KEY (`matching_job_id`) REFERENCES `matching_jobs`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_institution_run_request` ON `institution_pattern_runs` (`request_id`);--> statement-breakpoint
CREATE INDEX `idx_institution_run_source` ON `institution_pattern_runs` (`matching_job_id`,`scope_hash`,`pattern_version`,`projection_version`);--> statement-breakpoint
CREATE INDEX `idx_institution_run_mode` ON `institution_pattern_runs` (`mode`,created_at DESC,`id`);--> statement-breakpoint
CREATE TABLE `institution_pattern_years` (
	`run_id` text NOT NULL,
	`region_key` text NOT NULL,
	`org_key` text NOT NULL,
	`category` text NOT NULL,
	`fiscal_year` integer NOT NULL,
	`row_json` text NOT NULL,
	`high_projects` integer NOT NULL,
	PRIMARY KEY(`run_id`, `region_key`, `org_key`, `category`, `fiscal_year`),
	FOREIGN KEY (`run_id`) REFERENCES `institution_pattern_runs`(`id`) ON UPDATE no action ON DELETE no action
);
