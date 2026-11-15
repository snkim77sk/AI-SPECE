CREATE TABLE `g2b_recovery_attempts` (
	`id` text PRIMARY KEY NOT NULL,
	`generation_id` text NOT NULL,
	`run_id` text NOT NULL,
	`pass` integer NOT NULL,
	`page` integer NOT NULL,
	`state` text NOT NULL,
	`quota_day` text NOT NULL,
	`created_at` integer NOT NULL,
	`response_hash` text,
	`ordered_hash` text,
	`error` text,
	`conflict_json` text,
	FOREIGN KEY (`generation_id`) REFERENCES `g2b_recovery_generations`(`id`) ON UPDATE no action ON DELETE no action,
	FOREIGN KEY (`run_id`) REFERENCES `g2b_recovery_runs`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "g2b_recovery_attempt_bounds" CHECK("g2b_recovery_attempts"."pass" BETWEEN 1 AND 2 AND "g2b_recovery_attempts"."page" BETWEEN 1 AND 21),
	CONSTRAINT "g2b_recovery_attempt_state" CHECK("g2b_recovery_attempts"."state" IN ('reserved','failed','committed'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `g2b_recovery_page_attempt` ON `g2b_recovery_attempts` (`generation_id`,`pass`,`page`);--> statement-breakpoint
CREATE TABLE `g2b_recovery_generations` (
	`id` text PRIMARY KEY NOT NULL,
	`root_id` text NOT NULL,
	`diagnostic_id` text NOT NULL,
	`snapshot_json` text NOT NULL,
	`manifest_json` text NOT NULL,
	`day` text NOT NULL,
	`version` text NOT NULL,
	`window_from` text NOT NULL,
	`window_to` text NOT NULL,
	`state` text NOT NULL,
	`control_version` integer DEFAULT 0 NOT NULL,
	`pass` integer DEFAULT 1 NOT NULL,
	`page` integer DEFAULT 1 NOT NULL,
	`source_total` integer,
	`source_rows` integer DEFAULT 0 NOT NULL,
	`target_rows` integer DEFAULT 0 NOT NULL,
	`excluded_rows` integer DEFAULT 0 NOT NULL,
	`payload_bytes` integer DEFAULT 0 NOT NULL,
	`attempts` integer DEFAULT 0 NOT NULL,
	`current_run` text,
	`error` text,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`root_id`) REFERENCES `collection_sessions`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "g2b_recovery_state" CHECK("g2b_recovery_generations"."state" IN ('ready','running','paused','cancelled','uncertain','observed_consistent_needs_review')),
	CONSTRAINT "g2b_recovery_bounds" CHECK("g2b_recovery_generations"."pass" BETWEEN 1 AND 2 AND "g2b_recovery_generations"."page" BETWEEN 1 AND 21 AND "g2b_recovery_generations"."attempts" BETWEEN 0 AND 42 AND "g2b_recovery_generations"."source_rows" BETWEEN 0 AND 20000 AND "g2b_recovery_generations"."payload_bytes" BETWEEN 0 AND 67108864 AND "g2b_recovery_generations"."target_rows"+"g2b_recovery_generations"."excluded_rows"="g2b_recovery_generations"."source_rows")
);
--> statement-breakpoint
CREATE UNIQUE INDEX `g2b_recovery_one_root` ON `g2b_recovery_generations` (`root_id`);--> statement-breakpoint
CREATE TABLE `g2b_recovery_pages` (
	`generation_id` text NOT NULL,
	`pass` integer NOT NULL,
	`page` integer NOT NULL,
	`total` integer NOT NULL,
	`source_rows` integer NOT NULL,
	`target_rows` integer NOT NULL,
	`excluded_rows` integer NOT NULL,
	`ordered_hash` text NOT NULL,
	`response_hash` text NOT NULL,
	`response_bytes` integer NOT NULL,
	`committed_at` integer NOT NULL,
	PRIMARY KEY(`generation_id`, `pass`, `page`),
	FOREIGN KEY (`generation_id`) REFERENCES `g2b_recovery_generations`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "g2b_recovery_page_counts" CHECK("g2b_recovery_pages"."total" BETWEEN 0 AND 20000 AND "g2b_recovery_pages"."source_rows" BETWEEN 0 AND 999 AND "g2b_recovery_pages"."target_rows"+"g2b_recovery_pages"."excluded_rows"="g2b_recovery_pages"."source_rows")
);
--> statement-breakpoint
CREATE TABLE `g2b_recovery_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`generation_id` text NOT NULL,
	`owner` text NOT NULL,
	`state` text NOT NULL,
	`started_at` integer NOT NULL,
	`deadline` integer NOT NULL,
	`attempts` integer DEFAULT 0 NOT NULL,
	`in_flight` integer DEFAULT 0 NOT NULL,
	`error` text,
	FOREIGN KEY (`generation_id`) REFERENCES `g2b_recovery_generations`(`id`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "g2b_recovery_run_bounds" CHECK("g2b_recovery_runs"."attempts" BETWEEN 0 AND 8 AND "g2b_recovery_runs"."deadline"="g2b_recovery_runs"."started_at"+90000 AND "g2b_recovery_runs"."in_flight" IN (0,1)),
	CONSTRAINT "g2b_recovery_run_state" CHECK("g2b_recovery_runs"."state" IN ('active','uncertain','finished'))
);
--> statement-breakpoint
CREATE TABLE `g2b_recovery_source_rows` (
	`generation_id` text NOT NULL,
	`source_key` text NOT NULL,
	`page` integer NOT NULL,
	`ordinal` integer NOT NULL,
	`payload_hash` text NOT NULL,
	`excluded` text,
	`source_date` text NOT NULL,
	`date_basis` text NOT NULL,
	PRIMARY KEY(`generation_id`, `source_key`),
	FOREIGN KEY (`generation_id`) REFERENCES `g2b_recovery_generations`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE UNIQUE INDEX `g2b_recovery_order` ON `g2b_recovery_source_rows` (`generation_id`,`page`,`ordinal`);--> statement-breakpoint
CREATE TABLE `g2b_recovery_target_rows` (
	`generation_id` text NOT NULL,
	`source_key` text NOT NULL,
	`target_json` text NOT NULL,
	PRIMARY KEY(`generation_id`, `source_key`),
	FOREIGN KEY (`generation_id`) REFERENCES `g2b_recovery_generations`(`id`) ON UPDATE no action ON DELETE no action
);
