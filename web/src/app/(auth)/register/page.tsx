import { signUp } from "../actions";
import { AuthForm } from "../AuthForm";

export default function RegisterPage() {
  return <AuthForm mode="register" action={signUp} />;
}
