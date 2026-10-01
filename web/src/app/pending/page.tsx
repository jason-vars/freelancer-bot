import { redirect } from "next/navigation";
import { getProfile } from "@/lib/auth";

export default async function PendingPage() {
  const profile = await getProfile();
  if (!profile) redirect("/login");
  if (profile.status === "approved") redirect("/jobs");
  const rejected = profile.status === "rejected";
  return (
    <div className="card mx-auto mt-10 max-w-md p-6 text-center">
      <h1 className="mb-2 text-xl font-semibold">{rejected ? "Access declined" : "Waiting for approval"}</h1>
      <p className="text-sm text-muted">
        {rejected
          ? "An admin declined this account. Contact them if you think this is a mistake."
          : `Your account (${profile.email}) is registered. An admin has to approve it before you can see jobs. Reload this page once they have.`}
      </p>
    </div>
  );
}
