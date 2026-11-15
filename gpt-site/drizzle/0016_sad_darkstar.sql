CREATE TABLE `future_rank_rows` (
	`cursor_id` text NOT NULL,
	`run_id` text NOT NULL,
	`source_key` text NOT NULL,
	`rank_key` text NOT NULL,
	`row_json` text NOT NULL,
	PRIMARY KEY(`run_id`, `source_key`),
	FOREIGN KEY (`run_id`) REFERENCES `future_rank_runs`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `idx_future_rank_order` ON `future_rank_rows` (`run_id`,`rank_key`);--> statement-breakpoint
CREATE UNIQUE INDEX `idx_future_rank_cursor` ON `future_rank_rows` (`run_id`,`cursor_id`);--> statement-breakpoint
CREATE TABLE `future_rank_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`request_id` text NOT NULL,
	`mode` text NOT NULL,
	`institution_run_id` text NOT NULL,
	`institution_generation` integer NOT NULL,
	`institution_scope_hash` text NOT NULL,
	`g2b_epoch` integer NOT NULL,
	`budget_epoch` integer NOT NULL,
	`rank_version` text NOT NULL,
	`options_json` text NOT NULL,
	`scope_hash` text NOT NULL,
	`source_limit` integer NOT NULL,
	`state` text NOT NULL,
	`cursor_json` text,
	`examined` integer NOT NULL,
	`ranked` integer NOT NULL,
	`source_exhausted` integer NOT NULL,
	`lease` text,
	`lease_until` integer NOT NULL,
	`generation` integer NOT NULL,
	`error` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL,
	`completed_at` text,
	FOREIGN KEY (`institution_run_id`) REFERENCES `institution_pattern_runs`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "future_rank_limit" CHECK("future_rank_runs"."source_limit">=1 AND "future_rank_runs"."source_limit"<=500)
);
--> statement-breakpoint
CREATE UNIQUE INDEX `future_rank_runs_request_id_unique` ON `future_rank_runs` (`request_id`);--> statement-breakpoint
CREATE INDEX `idx_future_rank_mode` ON `future_rank_runs` (`mode`,created_at DESC,`id`);