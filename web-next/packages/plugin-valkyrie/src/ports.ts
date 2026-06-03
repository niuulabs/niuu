import type {
  AutonomyMode,
  EnvironmentSummary,
  FlockSummary,
  HuddleMessage,
  HuddleSummary,
  LearningRecord,
  ValkyrieDashboard,
  ValkyrieSignalEvent,
} from './domain';

export interface LearningDecisionRequest {
  learningId: string;
  reason: string;
  operatorId?: string;
}

export interface HuddleSendRequest {
  huddleId: string;
  body: string;
  directedTo?: string[];
  authorId?: string;
}

export interface AutonomyUpdateRequest {
  valkyrieId: string;
  mode: AutonomyMode;
  reason: string;
}

export interface IValkyrieService {
  getDashboard(): Promise<ValkyrieDashboard>;
  listEnvironments(): Promise<EnvironmentSummary[]>;
  getEnvironment(environmentId: string): Promise<EnvironmentSummary | null>;
  listFlocks(): Promise<FlockSummary[]>;
  getFlock(flockId: string): Promise<FlockSummary | null>;
  joinHuddle(huddleId: string): Promise<HuddleSummary>;
  sendHuddleMessage(request: HuddleSendRequest): Promise<HuddleMessage>;
  leaveHuddle(huddleId: string): Promise<HuddleSummary>;
  adoptLearning(request: LearningDecisionRequest): Promise<LearningRecord>;
  rejectLearning(request: LearningDecisionRequest): Promise<LearningRecord>;
  overrideLearning(request: LearningDecisionRequest): Promise<LearningRecord>;
  updateAutonomy(request: AutonomyUpdateRequest): Promise<ValkyrieDashboard>;
}

export type ValkyrieSignalListener = (event: ValkyrieSignalEvent) => void;

export interface IValkyrieSignalStream {
  subscribe(listener: ValkyrieSignalListener): () => void;
}
