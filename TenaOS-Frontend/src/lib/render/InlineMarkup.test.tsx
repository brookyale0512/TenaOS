import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { InlineMarkup } from "./InlineMarkup";

describe("InlineMarkup", () => {
  it("renders supported inline formatting without injecting HTML", () => {
    render(<InlineMarkup text={"Use **bold** and *emphasis* with `code`."} />);

    expect(screen.getByText("bold").tagName).toBe("STRONG");
    expect(screen.getByText("emphasis").tagName).toBe("EM");
    expect(screen.getByText("code").tagName).toBe("CODE");
  });

  it("treats malicious model output as inert text", () => {
    render(<InlineMarkup text={"<img src=x onerror=alert(1)> **safe** <script>alert(1)</script>"} />);

    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
  });

  it("renders clinical citations as badges without trusting citation text", () => {
    render(<InlineMarkup text={"Treat urgently *(WHO: <img onerror=alert(1)>)*"} clinicalCitations />);

    expect(screen.getByText(/WHO:/)).toBeInTheDocument();
    expect(screen.getByText(/<img onerror=alert\(1\)>/)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
  });

  it("renders lab notes as block text without injecting HTML", () => {
    render(<InlineMarkup text={"*(<b>note</b>)* Added **CBC**."} labNotes />);

    expect(screen.getByText("<b>note</b>")).toBeInTheDocument();
    expect(screen.getByText("CBC").tagName).toBe("STRONG");
    expect(document.querySelector("b")).toBeNull();
  });
});
