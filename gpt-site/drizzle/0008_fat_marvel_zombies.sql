CREATE TABLE `budget_epochs` (
	`mode` text PRIMARY KEY NOT NULL,
	`revision` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `matching_aliases` (
	`job_id` text NOT NULL,
	`alias` text NOT NULL,
	`source_key` text NOT NULL,
	PRIMARY KEY(`job_id`, `alias`, `source_key`)
);
--> statement-breakpoint
CREATE TABLE `matching_budgets` (
	`job_id` text NOT NULL,
	`source_key` text NOT NULL,
	`row_json` text NOT NULL,
	PRIMARY KEY(`job_id`, `source_key`)
);
--> statement-breakpoint
CREATE TABLE `matching_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`mode` text NOT NULL,
	`state` text NOT NULL,
	`phase` text NOT NULL,
	`g2b_epoch` integer NOT NULL,
	`budget_epoch` integer NOT NULL,
	`from_day` text NOT NULL,
	`to_day` text NOT NULL,
	`cursor` text NOT NULL,
	`partial` text,
	`budget_count` integer NOT NULL,
	`request_count` integer NOT NULL,
	`candidate_count` integer NOT NULL,
	`match_count` integer NOT NULL,
	`excluded_count` integer NOT NULL,
	`lease` text,
	`lease_until` integer NOT NULL,
	`error` text,
	`created_at` text NOT NULL,
	`updated_at` text NOT NULL
);
--> statement-breakpoint
CREATE TABLE `matching_requests` (
	`job_id` text NOT NULL,
	`request_key` text NOT NULL,
	`row_json` text NOT NULL,
	`missing_amounts` integer NOT NULL,
	`integrity` text NOT NULL,
	PRIMARY KEY(`job_id`, `request_key`)
);
--> statement-breakpoint
CREATE TABLE `matching_results` (
	`job_id` text NOT NULL,
	`request_key` text NOT NULL,
	`budget_key` text NOT NULL,
	`score` integer NOT NULL,
	`level` text NOT NULL,
	`row_json` text NOT NULL,
	PRIMARY KEY(`job_id`, `request_key`)
);
--> statement-breakpoint
CREATE INDEX `idx_matching_scores` ON `matching_results` (`job_id`,`score`,`request_key`);--> statement-breakpoint
INSERT INTO budget_epochs(mode,revision) VALUES('live',0),('synthetic',0);
