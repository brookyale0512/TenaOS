import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { createElement, type PropsWithChildren } from "react";

vi.mock("@/lib/api/client", () => ({
  openmrsClient: { post: vi.fn(), get: vi.fn() },
  setUnauthorizedHandler: vi.fn(),
}));

vi.mock("@/stores/uiStore", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const generateIdentifierMock = vi.fn();
vi.mock("@/lib/openmrs/idgen", async () => {
  const actual = await vi.importActual<typeof import("@/lib/openmrs/idgen")>("@/lib/openmrs/idgen");
  return {
    ...actual,
    generateIdentifier: (uuid: string) => generateIdentifierMock(uuid),
  };
});

import { openmrsClient } from "@/lib/api/client";
import { PatientRegistrationForm } from "./PatientRegistrationForm";

const post = openmrsClient.post as unknown as ReturnType<typeof vi.fn>;
const get = openmrsClient.get as unknown as ReturnType<typeof vi.fn>;

const REQUIRED_TYPE_UUID = "openmrs-id-type-uuid";
const SOURCE_UUID = "openmrs-id-source-uuid";
const LOCATION_UUID = "loc-uuid";

function setupRoutedClient(): void {
  get.mockImplementation((url: string) => {
    if (url === "/location") {
      return Promise.resolve({ data: { results: [{ uuid: LOCATION_UUID, display: "Demo Clinic" }] } });
    }
    if (url === "/patientidentifiertype") {
      return Promise.resolve({
        data: {
          results: [
            {
              uuid: REQUIRED_TYPE_UUID,
              display: "OpenMRS ID",
              name: "OpenMRS ID",
              required: true,
              uniquenessBehavior: "UNIQUE",
            },
            {
              uuid: "old-id-type",
              display: "Old Identification Number",
              name: "Old Identification Number",
              required: false,
            },
          ],
        },
      });
    }
    if (url === "/idgen/autogenerationoption") {
      return Promise.resolve({
        data: {
          results: [
            {
              uuid: "option-uuid",
              identifierType: { uuid: REQUIRED_TYPE_UUID },
              source: { uuid: SOURCE_UUID, name: "Generator for OpenMRS ID" },
              manualEntryEnabled: false,
              automaticGenerationEnabled: true,
              location: null,
            },
          ],
        },
      });
    }
    if (
      url === "/personattributetype" ||
      url === "/relationshiptype" ||
      url === "/patient"
    ) {
      return Promise.resolve({ data: { results: [] } });
    }
    return Promise.resolve({ data: { results: [] } });
  });
}

function renderForm() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const Wrapper = ({ children }: PropsWithChildren) =>
    createElement(QueryClientProvider, { client }, createElement(MemoryRouter, null, children));
  return render(<PatientRegistrationForm />, { wrapper: Wrapper });
}

async function fillDemographics(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByPlaceholderText("Given name"), "Ada");
  await user.type(screen.getByPlaceholderText("Family name"), "Lovelace");
  await user.click(screen.getByRole("combobox", { name: /Gender/i }));
  await user.click(screen.getByRole("option", { name: "Female" }));
  const dateInput = document.querySelector('input[type="date"]') as HTMLInputElement;
  await user.type(dateInput, "1990-12-10");
}

beforeEach(() => {
  vi.clearAllMocks();
  generateIdentifierMock.mockReset();
  // Safe default so tests that don't reach the identifiers step don't
  // accidentally trigger an unhandled rejection when the auto-generation
  // effect fires on mount.
  generateIdentifierMock.mockResolvedValue("AUTO-GENERATED");
  setupRoutedClient();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("PatientRegistrationForm", () => {
  it("blocks the demographics step when gender is not selected", async () => {
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByPlaceholderText("Given name"), "Ada");
    await user.type(screen.getByPlaceholderText("Family name"), "Lovelace");
    await user.click(screen.getByRole("button", { name: /next/i }));

    expect(await screen.findByText(/Gender required/i)).toBeInTheDocument();
  });

  it("auto-generates the required identifier on mount and posts a valid payload", async () => {
    const user = userEvent.setup();
    generateIdentifierMock.mockResolvedValueOnce("100AB7");
    post.mockResolvedValueOnce({ data: { uuid: "patient-uuid", display: "Ada Lovelace" } });
    renderForm();

    await fillDemographics(user);
    await user.click(screen.getByRole("button", { name: /next/i }));

    // Required identifier auto-generated; manual entry input is hidden.
    await waitFor(() => expect(generateIdentifierMock).toHaveBeenCalledWith(SOURCE_UUID));
    const generatedInput = (await screen.findByPlaceholderText(
      /Will be assigned by OpenMRS/i,
    )) as HTMLInputElement;
    expect(generatedInput.readOnly).toBe(true);
    await waitFor(() => expect(generatedInput.value).toBe("100AB7"));
    expect(screen.getByText(/^Generated$/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /next/i }));
    await user.click(screen.getByRole("combobox", { name: /Registration Location/i }));
    await user.click(screen.getByRole("option", { name: "Demo Clinic" }));
    await user.click(screen.getByRole("button", { name: /next/i }));
    await user.click(screen.getByRole("button", { name: /next/i }));
    await user.click(screen.getByRole("button", { name: /Register Patient/i }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/patient", expect.any(Object)));
    const [, payload] = post.mock.calls[0];
    expect(payload.identifiers).toHaveLength(1);
    expect(payload.identifiers[0]).toMatchObject({
      identifierType: REQUIRED_TYPE_UUID,
      identifier: "100AB7",
      location: LOCATION_UUID,
      preferred: true,
    });
    expect(payload.person.gender).toBe("F");
    expect(payload.person.birthdate).toBe("1990-12-10");
    expect(payload.person.birthdateEstimated).toBe(false);
  });

  it("surfaces server field errors back to the form when OpenMRS rejects the identifier", async () => {
    const user = userEvent.setup();
    generateIdentifierMock.mockResolvedValueOnce("100AB7");
    const fakeError = Object.assign(new Error("rejected"), {
      openmrsError: {
        title: "Validation failed",
        description: "Identifier already in use",
        fieldErrors: { "identifiers[0].identifier": ["Identifier already in use"] },
        globalErrors: [],
        status: 409,
      },
    });
    post.mockRejectedValueOnce(fakeError);
    renderForm();

    await fillDemographics(user);
    await user.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(generateIdentifierMock).toHaveBeenCalledWith(SOURCE_UUID));
    await waitFor(() =>
      expect((screen.getByPlaceholderText(/Will be assigned by OpenMRS/i) as HTMLInputElement).value).toBe(
        "100AB7",
      ),
    );

    await user.click(screen.getByRole("button", { name: /next/i }));
    await user.click(screen.getByRole("combobox", { name: /Registration Location/i }));
    await user.click(screen.getByRole("option", { name: "Demo Clinic" }));
    await user.click(screen.getByRole("button", { name: /next/i }));
    await user.click(screen.getByRole("button", { name: /next/i }));
    await user.click(screen.getByRole("button", { name: /Register Patient/i }));

    const failureAlert = await screen.findByRole("alert");
    expect(within(failureAlert).getByText(/Identifier already in use/i)).toBeInTheDocument();
  });
});
