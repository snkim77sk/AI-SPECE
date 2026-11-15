CREATE TABLE `budget_observation_epochs` (
	`mode` text PRIMARY KEY NOT NULL,
	`revision` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `budget_observations` (
	`mode` text NOT NULL,
	`service` text NOT NULL,
	`source_key` text NOT NULL,
	`snapshot_date` text NOT NULL,
	`payload_hash` text NOT NULL,
	`fiscal_year` integer NOT NULL,
	`first_seen` text NOT NULL,
	`last_seen` text NOT NULL,
	`last_session` text NOT NULL,
	PRIMARY KEY(`mode`, `service`, `source_key`, `snapshot_date`, `payload_hash`),
	FOREIGN KEY (`mode`,`service`,`source_key`,`payload_hash`) REFERENCES `budget_revisions`(`mode`,`service`,`source_key`,`payload_hash`) ON UPDATE no action ON DELETE no action,
	CONSTRAINT "budget_observations_qwgjk_only" CHECK("budget_observations"."service"='QWGJK')
);
--> statement-breakpoint
CREATE INDEX `idx_budget_observation_page` ON `budget_observations` (`mode`,`service`,`fiscal_year`,snapshot_date DESC,`source_key`,`payload_hash`);--> statement-breakpoint
CREATE INDEX `idx_budget_observation_baseline` ON `budget_observations` (`mode`,`service`,`source_key`,snapshot_date DESC,`payload_hash`);
--> statement-breakpoint
CREATE TRIGGER budget_observation_epoch_insert AFTER INSERT ON budget_observations BEGIN
  INSERT INTO budget_observation_epochs(mode,revision) VALUES(NEW.mode,1) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
END;
--> statement-breakpoint
CREATE TRIGGER budget_observation_epoch_update AFTER UPDATE ON budget_observations BEGIN
  INSERT INTO budget_observation_epochs(mode,revision) VALUES(OLD.mode,1) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
  INSERT INTO budget_observation_epochs(mode,revision) SELECT NEW.mode,1 WHERE NEW.mode<>OLD.mode ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
END;
--> statement-breakpoint
CREATE TRIGGER budget_observation_epoch_delete AFTER DELETE ON budget_observations BEGIN
  INSERT INTO budget_observation_epochs(mode,revision) VALUES(OLD.mode,1) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
END;
--> statement-breakpoint
CREATE TRIGGER budget_observation_revision_update AFTER UPDATE ON budget_revisions BEGIN
  INSERT INTO budget_observation_epochs(mode,revision) SELECT OLD.mode,1 WHERE EXISTS(SELECT 1 FROM budget_observations o WHERE o.mode=OLD.mode AND o.service=OLD.service AND o.source_key=OLD.source_key AND o.payload_hash=OLD.payload_hash) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
  INSERT INTO budget_observation_epochs(mode,revision) SELECT NEW.mode,1 WHERE NEW.mode<>OLD.mode AND EXISTS(SELECT 1 FROM budget_observations o WHERE o.mode=NEW.mode AND o.service=NEW.service AND o.source_key=NEW.source_key AND o.payload_hash=NEW.payload_hash) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
END;
--> statement-breakpoint
CREATE TRIGGER budget_observation_revision_delete AFTER DELETE ON budget_revisions BEGIN
  INSERT INTO budget_observation_epochs(mode,revision) SELECT OLD.mode,1 WHERE EXISTS(SELECT 1 FROM budget_observations o WHERE o.mode=OLD.mode AND o.service=OLD.service AND o.source_key=OLD.source_key AND o.payload_hash=OLD.payload_hash) ON CONFLICT(mode) DO UPDATE SET revision=revision+1;
END;
