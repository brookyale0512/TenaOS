export interface OpenMRSPatient {
  uuid: string;
  display: string;
  identifiers: Array<{
    uuid: string;
    identifier: string;
    identifierType: { uuid: string; display: string };
    preferred: boolean;
  }>;
  person: {
    uuid: string;
    display: string;
    gender: string;
    birthdate: string;
    birthdateEstimated: boolean;
    dead: boolean;
    age: number;
    preferredAddress?: {
      address1?: string;
      cityVillage?: string;
      stateProvince?: string;
      country?: string;
    };
    preferredName?: {
      givenName: string;
      familyName: string;
      middleName?: string;
      display: string;
    };
    attributes?: Array<{
      uuid: string;
      value: string;
      attributeType: { uuid: string; display: string };
    }>;
  };
  voided: boolean;
}

export interface OpenMRSVisit {
  uuid: string;
  display: string;
  patient: { uuid: string; display: string };
  visitType: { uuid: string; display: string };
  location: { uuid: string; display: string };
  startDatetime: string;
  stopDatetime?: string;
  encounters: Array<{ uuid: string; display: string }>;
  voided: boolean;
}

export interface OpenMRSEncounter {
  uuid: string;
  display: string;
  encounterDatetime: string;
  encounterType: { uuid: string; display: string };
  location: { uuid: string; display: string };
  patient: { uuid: string; display: string };
  obs: Array<OpenMRSObs>;
  voided: boolean;
}

export interface OpenMRSObs {
  uuid: string;
  display: string;
  concept: { uuid: string; display: string };
  value: string | number | { uuid: string; display: string };
  obsDatetime: string;
  voided: boolean;
}

export interface OpenMRSForm {
  uuid: string;
  display: string;
  name: string;
  description?: string;
  encounterType?: { uuid: string; display: string };
  version: string;
  published: boolean;
  retired: boolean;
}

export interface OpenMRSLocation {
  uuid: string;
  display: string;
  name: string;
  description?: string;
  parentLocation?: { uuid: string; display: string };
  tags?: Array<{ uuid: string; display: string }>;
  retired: boolean;
}

export interface OpenMRSQueueEntry {
  uuid: string;
  display: string;
  patient: { uuid: string; display: string };
  queue: { uuid: string; display: string; name: string };
  status: { uuid: string; display: string };
  priority: { uuid: string; display: string };
  priorityComment?: string;
  startedAt: string;
  endedAt?: string;
  waitTime?: number;
}

export interface OpenMRSQueue {
  uuid: string;
  display: string;
  name: string;
  description?: string;
  location: { uuid: string; display: string };
  service: { uuid: string; display: string };
  allowedPriorities: Array<{ uuid: string; display: string }>;
  allowedStatuses: Array<{ uuid: string; display: string }>;
}

export interface OpenMRSVisitType {
  uuid: string;
  display: string;
  name: string;
  description?: string;
  retired: boolean;
}

export interface PaginatedResponse<T> {
  results: T[];
  links?: Array<{ rel: string; uri: string }>;
  totalCount?: number;
}
