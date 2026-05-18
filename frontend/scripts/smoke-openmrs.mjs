const baseUrl = process.env.OPENMRS_BASE_URL ?? "http://localhost:18080/openmrs";
const username = process.env.OPENMRS_USERNAME ?? "admin";
const password = process.env.OPENMRS_PASSWORD ?? "Admin123";
const auth = `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;

async function getJson(path) {
  const response = await fetch(`${baseUrl}${path}`, {
    headers: { Authorization: auth },
  });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}`);
  }
  return response.json();
}

async function countVisits(patientUuid) {
  const visits = await getJson(`/ws/rest/v1/visit?patient=${patientUuid}&includeInactive=true&limit=100&v=custom:(uuid)`);
  return (visits.results ?? []).length;
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

const session = await getJson("/ws/rest/v1/session");
assert(Object.prototype.hasOwnProperty.call(session, "authenticated"), "REST session did not return session payload");

await getJson("/ws/fhir2/R4/metadata");

const patients = await getJson("/ws/rest/v1/patient?q=TAT&limit=3&v=custom:(uuid,display)");
assert((patients.results ?? []).length > 0, "Expected imported TenaOS patients for q=TAT");

const patient = patients.results[0];
const visitCountBeforeOpen = await countVisits(patient.uuid);
await getJson(`/ws/rest/v1/patient/${patient.uuid}?v=full`);
const visitCountAfterOpen = await countVisits(patient.uuid);
assert(visitCountAfterOpen === visitCountBeforeOpen, "Opening a patient must not create a visit");

const encounters = await getJson(
  `/ws/rest/v1/encounter?patient=${patient.uuid}&limit=10&v=custom:(uuid,encounterDatetime,obs:(uuid,concept:(uuid,display),value))`,
);
assert((encounters.results ?? []).some((encounter) => (encounter.obs ?? []).length > 0), "Expected patient encounters with observations");
assert((encounters.results ?? []).some((encounter) => (encounter.obs ?? []).some((obs) => /haemoglobin|glucose|cd4|viral load|serum/i.test(obs.concept.display))), "Expected lab-like observations for timeline/labs");
assert((encounters.results ?? []).some((encounter) => (encounter.obs ?? []).some((obs) => /medication|amoxicillin|prophylaxis|imported text/i.test(obs.concept.display))), "Expected medication-like observations for medication display");

const forms = await getJson("/ws/rest/v1/form?limit=1&v=custom:(uuid,name,published,encounterType:(uuid,display))");
assert((forms.results ?? []).length > 0, "Expected at least one OpenMRS form");
assert(forms.results[0].uuid, "Expected form selector to receive form UUIDs");

console.log(`OpenMRS smoke test passed for ${patient.display}`);
